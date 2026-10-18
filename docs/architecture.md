# Multi-Agent Research & Report Platform: Architecture, Workflow and Tooling

Oct 18, 2026 · @Maro

## 1. Project overview

The platform takes one research question and returns a cited report written by cooperating AI agents, where every claim links to the exact source passage behind it and every agent step is visible in a live trace. "Atlas Research" is a placeholder name.

**The problem.** A single LLM call answers research questions fluently but cannot prove its claims, often invents citations, and hides its reasoning. A multi-agent system with an independent verifier fixes the three weaknesses: planning, evidence gathering and checking are separate jobs done by separate agents.

**Goals**

- Produce reports in which at least 90% of citations actually support the claim they are attached to (measured, not assumed).
- Show every step (plan, searches, fetched pages, verification verdicts, drafts) in a trace the user can replay.
- Support Arabic and English questions, sources and reports.
- Enforce a per-run budget for cost, tokens and time.
- Prove the design with an evaluation: single LLM baseline vs. multi-agent without critic vs. full system.

**Non-goals (to protect scope)**

- No custom model training.
- No general web crawler; the platform uses a search API plus a fetch layer.
- No multi-tenant billing or team accounts in version 1.

**Who it is for.** Version 1 targets students writing literature reviews and freelancers or founders doing market research. A narrow audience makes it a product rather than a demo, and it keeps the benchmark questions realistic.

**Final deliverables**

| Deliverable | What it is |
| --- | --- |
| Web app | React UI with question form, plan approval, live trace viewer, report page with clickable citations |
| Backend | FastAPI service, job queue, orchestrator and agents, Postgres and vector store |
| Evaluation harness | 30 to 50 benchmark questions, scoring scripts, ablation results table |
| Deployed demo | Public URL plus a 3 minute demo video |
| Documentation | README, this architecture document, and a tradeoffs write-up |

## 2. System architecture

The system is a web app in front of a queue and a worker: the API accepts requests and streams events, while a separate worker runs the agents, so a long research run never blocks a web request.

&#91;embedded content: system architecture · 4 layers\]

Read the drawing top to bottom: the browser talks to the API, the API and the worker exchange jobs and events through Redis, and the worker reaches four outside services through its own client layers. The API also reads Postgres directly to serve run history, reports and claim details.

### Components and responsibilities

| Component | Responsibility | Talks to |
| --- | --- | --- |
| Browser (React) | Question form, plan approval, live trace, report with citations | API over REST and SSE |
| FastAPI service | Validates requests, creates runs, enqueues jobs, streams events, serves reports and exports | Browser, Redis, Postgres |
| Redis | Job queue, page and search cache, live event relay between worker and API | API, worker |
| Worker | Runs one research run end to end; owns the agent graph and the Orchestrator | Redis, all client layers |
| LLM client | One interface (generate, generate structured output, embed) over several model providers | LLM providers |
| Search and fetch tools | Search API calls, polite page fetching, text extraction, language detection | Web search and sites |
| Vector tool | Creates embeddings, stores and retrieves passages with filters, reranks | Qdrant |
| Data access layer | Typed reads and writes for runs, plans, evidence, claims, verdicts and events | Postgres |
| Postgres | System of record for everything that must be queried, audited or replayed | Worker, API |
| Qdrant | Vector index of evidence passages, filtered by run | Worker |

### Design principles

- **API and worker are separate processes.** Web requests stay fast, and workers can be scaled or restarted without dropping connections.
- **Everything is an event.** Each agent step is written to the events table and relayed live, so the trace viewer, cost reports and evaluation all read the same record.
- **Agents depend on interfaces, not vendors.** They call the LLM client and tool classes, so models and search providers can be swapped in configuration.
- **Steps are idempotent and checkpointed.** A crashed worker resumes from the last completed step, and repeated fetches hit the cache instead of the network.

## 3. Agent design

Five roles do the work, and only the Orchestrator is mostly ordinary code. Each agent has one job, a typed input, a typed output, and its own model tier so cheap steps do not burn expensive tokens.

| Agent | Job | Model tier | Tools it may call | Output |
| --- | --- | --- | --- | --- |
| Orchestrator | Runs the state machine, enforces budgets, decides when to stop | None for control flow, small model for the sufficiency check | Queue, database, all agents | Run state and events |
| Planner | Turns the question into sub-questions and a report outline | Strong reasoning model | None | Plan (JSON) |
| Researcher (N in parallel) | Finds, reads and stores evidence for one sub-question | Small fast model for extraction, medium for query writing | web\_search, fetch\_page, vector\_search | Evidence rows and findings |
| Writer | Drafts atomic claims, then the report prose | Strong model | read\_evidence | Claims and report markdown |
| Critic | Checks each claim against its cited passages and finds gaps | Strong model, ideally a different provider than the Writer | read\_evidence | Verdicts and gap list |

### Orchestrator

It owns the run lifecycle and nothing else. It creates the Planner task, fans out Researcher tasks, collects results, starts the Writer, sends claims to the Critic, and loops until a stop rule fires (section 4). It tracks tokens, dollars and wall-clock time per run and refuses to start a new step that would break the budget. Keeping it as deterministic code makes runs reproducible and testable.

### Planner

- **Input:** the question, detected language (Arabic or English), optional constraints such as date range, source types, and depth (quick, standard, deep).
- **Output:** 3 to 7 sub-questions, each with a rationale and 2 to 3 seed search queries, plus a proposed report outline that maps sections to sub-questions.
- **Checkpoint:** the plan is shown to the user, who can edit, remove or add sub-questions before any search money is spent.
- **Failure to guard:** too many or overlapping sub-questions. The schema caps the count and a validation step rejects near-duplicates by embedding similarity.

### Researcher

One instance runs per sub-question, concurrently. Its loop is: write queries, call search, rank and filter results, fetch the best pages, split them into passages, keep the passages that answer the sub-question, and store each as an Evidence row with source metadata. It stops when it has enough high-quality passages or hits its per-sub-question limits for pages and tokens.

- **Output per passage:** URL, title, publisher, retrieved time, passage text, character offsets in the cleaned page, a relevance score, and an embedding.
- **Source quality:** a simple scoring step prefers primary sources, official documentation and peer-reviewed or well-known publishers, and penalizes duplicates and thin pages.
- **Security rule:** fetched page text is data, never instructions. The Researcher prompt states this, and page text is wrapped in delimiters so embedded commands like "ignore your instructions" are not followed.

### Writer

The Writer works in two passes so that citations cannot be invented. Pass one produces **atomic claims**: one factual sentence each, with a list of evidence ids it relies on. Pass two turns approved claims into readable prose and attaches citation markers. The Writer may cite only evidence ids it was handed; any other citation fails validation automatically before the Critic even runs.

### Critic

The Critic is the part that makes the system trustworthy. For every claim it reads the cited passages and returns one verdict:

- **supported:** the passages state or directly entail the claim.
- **partial:** the passages support part of it; the Critic proposes a narrower wording.
- **unsupported:** the passages do not back the claim.
- **contradicted:** another passage in the evidence store conflicts with it.

It also lists **gaps**: sub-questions with thin evidence, and conflicts between sources that the report should mention. Gaps become new Researcher tasks. Using a different model provider than the Writer reduces the chance that both share the same blind spot.

### Prompting and output discipline

- Every agent returns JSON validated by a Pydantic model; invalid output triggers one repair attempt, then a logged failure.
- Prompts live in versioned files in the repo, not inline strings, so evaluation results can be tied to a prompt version.
- Temperature is low (0 to 0.3) for Planner, Critic and extraction; slightly higher only for the Writer's prose pass.
- Each prompt includes two or three short worked examples of correct output, including one edge case.

## 4. End-to-end workflow

A run is a fixed sequence of stages with one verification loop: research and drafting happen once, then the Critic and a revision step repeat until the claims hold up or a limit ends the loop.

&#91;embedded content: run lifecycle · 7 steps, 1 decision, 1 loop\]

The highlighted box is the deliverable. The only point where the run waits for a person is plan approval (step 3), and it can be switched off for fully automatic runs.

### Stage by stage

1. **Submit.** The browser calls `POST /api/runs`. The API validates the request, estimates cost from the depth setting, inserts the run row, enqueues the job and returns the run id. Event: `run_started`.
2. **Plan.** The Planner returns 3 to 7 sub-questions with seed queries and a report outline. A validator rejects near-duplicate sub-questions. Event: `plan_created`.
3. **Approve.** The run pauses in `awaiting_approval`. The user edits, removes or adds sub-questions, then approves. Event: `plan_approved`.
4. **Research.** One Researcher per sub-question runs concurrently (inner loop below). Each stored passage emits `evidence_stored`.
5. **Draft.** The Writer produces atomic claims, each with evidence ids. A validator checks that every cited id exists and belongs to this run. Event: `claim_drafted`.
6. **Verify and decide.** The Critic returns a verdict for each claim and a list of gaps and conflicts (events `verdict`, `gap_found`). If every claim is supported, or deliberately framed as a source disagreement, the run moves to step 7. Otherwise, subject to the stop rules below, it revises first:
   - Targeted Researchers fill only the gaps.
   - The Writer rewrites partial claims, drops unsupported ones, and turns contradictions into explicit "sources disagree" claims.
   - Control returns to the start of step 6 so every changed claim is re-checked.
7. **Finalize.** The Writer turns approved claims into prose with citation markers, and a Risks section is generated from the remaining gaps and conflicts. The report is stored, exports are available, and the run ends with `run_completed`.

### Inside one Researcher

1. Write 2 to 3 search queries from the sub-question and its seed queries.
2. Call the search tool and merge results across queries.
3. Rank results by relevance and source quality; drop duplicates and thin pages.
4. Fetch the top pages (cache first), extract the main text, detect the language.
5. Split text into passages with character offsets.
6. Embed passages, score them against the sub-question, keep only the relevant ones.
7. Store sources, evidence rows and vectors; emit events.
8. Stop when enough strong passages exist or the page and token limits for this sub-question are reached.

### Stop rules

The loop ends at the first of these:

- **Success:** every claim is supported or explicitly framed as a source disagreement.
- **Round limit:** 3 Critic rounds by default, configurable per depth setting.
- **No progress:** a round changes no verdicts and adds no new supported claims.
- **Budget or time cap:** the next step would exceed the dollar, token or time limit.
- **Cancellation:** the user stops the run.

When a limit ends the loop, the report is still produced: claims that never became supported are removed from the body and listed under Risks as unverified, so the report is honest about what it could not confirm.

## 5. Data model

Postgres stores everything that must be queried, audited or replayed; the vector database stores only embeddings plus the ids that point back to Postgres. The event log is the backbone: the trace viewer, cost reports and evaluation all read from it.

### Postgres tables

| Table | Key columns | Purpose |
| --- | --- | --- |
| runs | id, user\_id, question, language, depth, status, budget\_usd, budget\_tokens, budget\_seconds, spent\_usd, spent\_tokens, created\_at, finished\_at | One row per research run |
| sub\_questions | id, run\_id, text, rationale, priority, approved, status | The approved plan |
| sources | id, run\_id, url, title, publisher, retrieved\_at, content\_hash, quality\_score | One row per fetched page |
| evidence | id, source\_id, sub\_question\_id, passage\_text, start\_char, end\_char, relevance\_score | A stored passage that may back a claim |
| claims | id, run\_id, section, text, status, version | Atomic claims written by the Writer |
| claim\_evidence | claim\_id, evidence\_id | Which passages each claim cites |
| verdicts | id, claim\_id, round, verdict, rationale, suggested\_rewrite | Critic output per round |
| reports | id, run\_id, version, markdown, created\_at | Final and intermediate reports |
| events | id, run\_id, seq, ts, agent, type, payload (jsonb), tokens\_in, tokens\_out, cost\_usd, latency\_ms | Append-only trace of every step |

Design choices worth knowing:

- **content\_hash** on sources lets the cache skip a page already fetched in this or a recent run.
- **start\_char and end\_char** make citations exact: the UI can highlight the passage inside the cleaned page text.
- **claims and verdicts are versioned by round**, so you can show how a claim was rewritten after the Critic rejected it.
- **events is append-only**, so a run can be replayed step by step and evaluated after the fact.

### Vector store

One Qdrant collection named `evidence` holds one point per passage. The vector comes from a multilingual embedding model so Arabic and English passages live in the same space. The payload carries `run_id`, `evidence_id`, `source_id`, `language` and `sub_question_id`, so searches can be filtered to a single run. Index only passages that survived the relevance filter to keep the collection small and retrieval sharp.

### Event schema

Every agent step emits one event with the same envelope. The streaming API sends exactly these objects to the browser.

```json
{
  "run_id": "run_01",
  "seq": 42,
  "ts": "2026-10-18T14:03:11Z",
  "agent": "researcher",
  "type": "tool_call",
  "payload": {
    "tool": "fetch_page",
    "args": {"url": "https://example.org/report"},
    "result_summary": "Fetched 4,210 words, kept 6 passages"
  },
  "tokens_in": 1850,
  "tokens_out": 240,
  "cost_usd": 0.0031,
  "latency_ms": 1480
}
```

Event types: `run_started`, `plan_created`, `plan_approved`, `llm_call`, `tool_call`, `evidence_stored`, `claim_drafted`, `verdict`, `gap_found`, `report_version`, `budget_warning`, `run_completed`, `run_failed`.

## 6. Tools and libraries explained

Each row names the tool, what it is in plain words, the exact job it does in this project, and the alternative if you want to swap it. Defaults are chosen to keep the stack small, popular with employers, and free or cheap to run during development.

### Core language and data validation

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| Python 3.12 | The main language for AI work | All agents, tools and the API are Python; best library support for LLMs and retrieval | None needed |
| Pydantic v2 | Library that defines data shapes as classes and validates them | Defines the schema of every agent output (Plan, Claim, Verdict) and every API request, so malformed LLM output is caught immediately | dataclasses plus manual checks |
| asyncio and httpx | Python's async runtime and an async HTTP client | Lets many Researchers fetch pages at the same time without threads | aiohttp |
| uv | Fast Python package and environment manager | Reproducible installs with a lockfile, fast CI | pip with venv, Poetry |

### Backend and job processing

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| FastAPI | Async Python web framework with automatic API docs | Serves the REST API and the streaming endpoint; Pydantic integration gives validated requests for free | Flask, Django REST |
| Uvicorn | ASGI server | Runs FastAPI in development and production | Hypercorn |
| Redis | In-memory data store | Backs the job queue, caches fetched pages and search results, and relays live events to the streaming endpoint | Valkey, RabbitMQ for the queue only |
| Arq | Small async job queue built on Redis | Runs each research run in a background worker so HTTP requests return instantly; async-native, fits our asyncio code | Celery (heavier, more features), Dramatiq |
| SQLAlchemy 2 and Alembic | Database toolkit and its migration tool | Typed access to Postgres, and versioned schema changes as the data model evolves | SQLModel, raw SQL with asyncpg |

### Agent orchestration and LLM access

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| LangGraph | Library that models an agent workflow as a graph of steps with shared state | Gives the loop Planner, Researchers, Writer, Critic with branching, retries, checkpoints, and a built-in pause for the plan-approval step | A hand-written state machine; building this yourself first is an excellent learning exercise |
| Anthropic SDK | Official client for Claude models | Primary model provider; supports structured tool use and long context | OpenAI SDK |
| OpenAI SDK | Official client for OpenAI models | Second provider so the Critic can run on a different model than the Writer | Google Gemini SDK |
| LiteLLM (optional) | One interface that talks to many LLM providers | Lets you swap models by changing a config string; also tracks cost per call | A thin LLMClient interface you write yourself |
| Ollama or vLLM (optional) | Tools to run open models locally or on your own server | Cheap development runs and an offline mode; vLLM is for serving a model at higher throughput | llama.cpp |

Keep every agent behind a small `LLMClient` interface (generate, generate\_structured, embed). The orchestration library then never touches a provider SDK directly, which is what makes the "pluggable model layer" feature real.

### Search, fetching and text extraction

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| Tavily or Brave Search API | Search engines exposed as APIs | Researchers call one to turn a query into ranked URLs without scraping search pages; Tavily returns cleaned snippets built for LLM use | Serper, SerpAPI, Exa |
| httpx (fetch layer) | HTTP client | Downloads pages with timeouts, retries, polite headers and robots.txt respect | requests |
| trafilatura | Library that extracts the main article text from HTML | Strips menus, ads and footers so only meaningful text becomes evidence | readability-lxml, newspaper3k |
| Playwright | Browser automation | Fallback for pages that render content with JavaScript | Selenium |
| PyMuPDF | PDF text extraction library | Reads papers and reports that are PDFs, which are common in research | pdfplumber, unstructured |
| lingua-py | Language detection library | Detects Arabic versus English per page and per passage so the right embedding and prompt rules apply | langdetect |

### Retrieval: embeddings, vector search, reranking

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| BGE-M3 (via sentence-transformers or FlagEmbedding) | Open multilingual embedding model | Turns passages into vectors; supports Arabic and English in one space, and also produces sparse signals for hybrid search | multilingual-e5, or a hosted embedding API |
| Qdrant and qdrant-client | Open-source vector database and its Python client | Stores passage vectors with filters per run; easy to run in Docker; supports hybrid dense plus sparse queries | pgvector (one fewer service), Weaviate, Chroma for quick prototypes |
| bge-reranker-v2-m3 | Cross-encoder model that scores how well a passage answers a query | Reorders the top retrieved passages so the Writer and Critic see the best evidence first | A hosted reranker API, or skip it in version 1 |
| rank-bm25 | Keyword ranking algorithm library | Adds exact-term matching (names, numbers) that embeddings can miss | Qdrant sparse vectors, PostgreSQL full-text search |

### Storage

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| PostgreSQL | Relational database | System of record for runs, plans, evidence, claims, verdicts and the event log; jsonb holds flexible event payloads | SQLite for very early prototyping only |
| Object storage (local disk first, S3-compatible later) | File storage | Holds cleaned page text and exported reports | Postgres large objects |

### Frontend

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| React and TypeScript | UI library and typed JavaScript | The whole interface; TypeScript catches mismatches with the API types | Vue, Svelte |
| Vite | Frontend build tool | Fast dev server and builds | Next.js if you want server rendering |
| Tailwind CSS and shadcn/ui | Utility CSS and a set of accessible components | Clean, consistent UI quickly without hand-writing CSS | Material UI |
| TanStack Query | Data fetching and caching library | Loads runs, reports and history with automatic refetching | SWR, plain fetch |
| Server-Sent Events (EventSource or fetch-event-source) | One-way live stream from server to browser | Pushes agent events to the trace viewer as they happen; simpler than WebSockets for one-way updates | WebSockets |
| React Flow | Library for node-and-edge diagrams | Renders the agent graph in the trace viewer | D3 for custom drawing |
| react-markdown | Markdown renderer for React | Displays the report and turns citation markers into clickable chips | MDX |

### Observability, testing and delivery

| Tool | What it is | Why we use it here | Alternative |
| --- | --- | --- | --- |
| Langfuse | Open-source tracing and evaluation platform for LLM apps | Second view of every LLM call with prompts, outputs, cost and latency; compare prompt versions | LangSmith, Arize Phoenix |
| OpenTelemetry | Standard for tracing and metrics | Traces API requests and worker jobs end to end | Plain structured logs |
| structlog | Structured logging library | JSON logs tagged with run\_id, so one run's logs are easy to filter | loguru |
| pytest, pytest-asyncio, respx | Test runner, async support, and an HTTP mocking library | Unit tests for agents with mocked LLM and network calls | vcrpy to record real calls |
| Ruff, mypy, pre-commit | Linter and formatter, type checker, and git hooks | Keeps code quality consistent and catches bugs early | Black plus flake8 |
| Docker and Docker Compose | Containers and a way to run several at once | One command starts API, worker, Postgres, Redis, Qdrant and frontend | Podman |
| GitHub Actions | Automation that runs on every push | Runs lint, tests and a small evaluation subset on each pull request | GitLab CI |
| WeasyPrint and python-docx | HTML-to-PDF and Word file libraries | Report export to PDF and DOCX | Pandoc |

## 7. API and streaming design

The API is small on purpose: start a run, watch it, approve the plan, get the report. Long work happens in a background worker, and the browser follows progress through a Server-Sent Events stream that can resume after a dropped connection.

### Endpoints

| Method and path | Purpose | Notes |
| --- | --- | --- |
| POST /api/runs | Start a run | Body: question, language (optional), depth, budget\_usd, require\_plan\_approval. Returns run\_id immediately |
| GET /api/runs | List past runs | Paginated; powers the history page |
| GET /api/runs/{id} | Run status and summary | Status, spend so far, current stage |
| GET /api/runs/{id}/events | Live event stream (SSE) | Supports resume with the Last-Event-ID header, which is the event seq |
| POST /api/runs/{id}/plan/approve | Approve or edit the plan | Body: the edited list of sub-questions |
| POST /api/runs/{id}/cancel | Stop a run | Worker stops at the next safe point and keeps partial results |
| GET /api/runs/{id}/report | Final report | Query: format = md, pdf or docx |
| GET /api/runs/{id}/claims/{claim\_id} | Claim detail | Returns the cited passages, source links and Critic verdicts |
| POST /api/runs/{id}/rerun | Rerun with other settings | Body: model overrides; links the new run to the original for comparison |
| GET /api/health | Health check | Used by Docker and the deployment platform |

### Run states

`queued`, `planning`, `awaiting_approval`, `researching`, `drafting`, `verifying`, `completed`, `failed`, `cancelled`, `budget_exhausted`. A run moves forward through these states and may loop between `drafting`, `verifying` and `researching` during the critic loop.

### How streaming works

1. The worker writes every event to the `events` table and publishes it on a Redis channel named after the run.
2. The API's SSE endpoint first replays any stored events after the client's Last-Event-ID, then subscribes to the Redis channel for new ones.
3. The browser receives each event as it happens; if the connection drops, EventSource reconnects and the replay fills the gap with no lost steps.

### Job queue behavior

- Starting a run creates a database row and enqueues one Arq job, `execute_run(run_id)`.
- Inside the job, Researchers run as asyncio tasks limited by a semaphore (for example 4 at a time) plus global rate limits for search and LLM calls, so a deep run cannot exhaust provider quotas.
- LangGraph checkpoints are stored in Postgres after each node, so a crashed worker can resume a run from its last completed step instead of starting over.
- Every external call has a timeout and a bounded retry with exponential backoff; repeated failures are logged as events and the step is skipped, not fatal.
- Cancel and budget checks run between steps; a step in flight finishes before the run stops.

## 8. Frontend and trace viewer

The interface has five screens. The trace viewer and the clickable-citation report are the two that make the project memorable in a demo, so spend design time there.

| Screen | What the user sees and does | Main components |
| --- | --- | --- |
| 1. New research | Types a question, picks depth (quick, standard, deep), sets a budget cap in dollars, toggles plan approval | Question form, budget slider, cost estimate |
| 2. Plan review | Sees the proposed sub-questions and outline; edits, deletes, reorders or adds items, then approves | Editable list, "approve and start" button |
| 3. Live run (trace viewer) | Watches agents work in real time | Agent graph, event timeline, budget meter, expandable tool calls |
| 4. Report | Reads the final report; clicks any citation to see the evidence | Markdown view, citation chips, evidence side panel, verdict badges, export menu |
| 5. History and compare | Browses past runs; opens two runs side by side | Run table, diff view of two reports, cost and quality summary |

### The live trace viewer

It has three coordinated panels fed by the same event stream.

- **Agent graph (React Flow):** nodes for Planner, each Researcher, Writer and Critic. The active node pulses, finished nodes show a check, and failed ones show a warning. Edges show who handed work to whom, including the Critic-to-Researcher loop when gaps are found.
- **Event timeline:** a scrolling list of events with agent, action, duration and cost. Clicking an event expands the prompt summary, tool arguments, and result, with the raw JSON available for debugging.
- **Budget meter:** live spend in dollars, tokens used, elapsed time, and the share of the cap consumed. It turns amber at 80% and shows a clear message if the run stops for budget.

### The report view

- Each sentence carries a small citation chip such as \[3\]. Clicking it opens a side panel with the source title, link, retrieval time, and the passage highlighted in context using the stored character offsets.
- A verdict badge shows whether the Critic marked the claim supported, partial (with the rewritten wording), or removed. A toggle hides unsupported claims entirely.
- A "coverage" strip lists each sub-question with the number of claims and sources backing it, so the user can see where the report is thin.
- An export menu produces Markdown, PDF or DOCX, and a "share" link produces a read-only public page.

### Frontend behavior rules

- All live data arrives through one EventSource hook; screens read from a small client store (for example Zustand) updated by that hook, so the graph, timeline and budget meter never disagree.
- The UI renders Arabic correctly with right-to-left layout when the report language is Arabic, including mixed Arabic and English text.
- Every loading, empty and error state is designed, because a demo with a blank screen while an agent thinks looks broken.

## 9. Worked example: one run from question to report

This walk-through shows what the system does and what the user receives. It is illustrative: the sources, counts and costs are placeholders to show the shape of a run, not real results.

**Input.** Question: "Which type of language model should a small business use for Arabic customer support, and what are the risks?" Depth: standard. Budget cap: $1.50. Plan approval: on.

### Step-by-step trace

| # | Agent | What happens | Result |
| --- | --- | --- | --- |
| 1 | Orchestrator | Creates the run, detects Arabic and English, enqueues the job | run\_started |
| 2 | Planner | Splits the question into sub-questions: model options, Arabic dialect quality, cost and hosting, data privacy, evaluation methods | 5 sub-questions and an outline |
| 3 | User | Reviews the plan, deletes one sub-question, adds "integration with WhatsApp" | plan\_approved |
| 4 | Researchers (5 in parallel) | Each writes queries, searches, ranks, fetches, extracts and stores passages | 14 searches, 31 pages fetched, 24 kept, 96 passages stored |
| 5 | Writer, pass 1 | Drafts atomic claims, each tied to evidence ids | 22 claims |
| 6 | Critic, round 1 | Checks each claim against its passages | 15 supported, 4 partial, 2 unsupported, 1 contradicted; 2 gaps found |
| 7 | Researchers (targeted) | Fill the 2 gaps only | 2 searches, 6 new passages |
| 8 | Writer, revision | Rewrites 4 partial claims, drops 2 unsupported, turns the contradiction into a "sources disagree" claim, adds 3 claims from new evidence | 23 claims |
| 9 | Critic, round 2 | Re-checks changed and new claims | 23 of 23 supported |
| 10 | Writer, final pass | Turns claims into prose with citation markers | Report v3 |
| 11 | Orchestrator | Stores the report and closes the run | run\_completed, $0.84, 3 min 40 s |

The user sees steps 2 to 11 live in the trace viewer; step 3 is the only moment the run waits for them.

### Critic round 1 in detail

| Claim (shortened) | Verdict | What happens next |
| --- | --- | --- |
| Hosted models are the fastest to deploy | supported | Kept |
| Small open models match large hosted models on dialect text | unsupported | The cited passage only discusses formal Arabic; claim is dropped |
| Fine-tuning always lowers cost | contradicted | Another source shows cases where it does not; reframed as "sources disagree" |
| Customer data can stay on-premises with open models | partial | The passage supports it only with extra infrastructure; wording narrowed |

### Final report (what the user receives)

```markdown
# Language models for Arabic customer support

## Summary
Small businesses have two practical paths: a hosted model accessed
through an API, or a smaller open model run on their own hardware.
Hosted models are faster to launch [1][2]; self-hosting keeps customer
data on the company's own servers but needs extra infrastructure [3].

## Dialect quality
Performance on formal Arabic does not reliably predict performance on
regional dialects [4]. Test with real customer messages before choosing [5].

## Cost and hosting
Sources disagree on whether fine-tuning lowers total cost: one reports
savings at high volume [6], another reports higher upkeep costs [7].

## Risks and open questions
- Evidence on dialect-specific accuracy is limited (only 2 sources).
- Pricing changes often; figures should be rechecked before purchase.

## Sources
[1] Example Cloud Docs, "Deployment guide", retrieved 2026-10-18
[2] Example Benchmark Group, "Latency comparison", retrieved 2026-10-18
[3] ...
```

Every bracketed number opens the exact passage in the side panel, and the Risks section is generated from the Critic's gap and conflict list, so honest uncertainty appears in the report instead of being smoothed over.

### Run summary shown at the end

| Metric | Illustrative value |
| --- | --- |
| Total cost | $0.84 of $1.50 budget |
| Wall-clock time | 3 min 40 s |
| Sources used | 24 pages, 96 passages stored, 21 cited |
| Claims | 23 final, 2 dropped, 4 rewritten, 1 flagged as a disagreement |
| Critic rounds | 2 |

## 10. Evaluation plan

The evaluation is what turns this from a demo into engineering evidence: it measures whether the Critic and the multi-agent design actually improve reports, and by how much.

### Benchmark construction

- **Size and mix:** 30 to 50 questions. Roughly a third factual synthesis, a third comparisons, a third trend or "state of the field" questions. At least 10 in Arabic.
- **Gold data per question:** a list of 5 to 10 key facts a good answer should contain, plus 2 to 4 reference sources you checked by hand.
- **Hard cases on purpose:** 5 questions with no good answer on the web (the system should say so), and 5 with conflicting sources (the system should report the conflict).
- **Reproducibility:** a record-and-replay mode stores search results and fetched pages once, so every configuration is tested on identical web content. Without it, web changes make comparisons meaningless.
- **Variance:** run each configuration 3 times and report the mean and spread, because LLM outputs vary between runs.

### Metrics

| Metric | How it is measured | Why it matters |
| --- | --- | --- |
| Citation precision | Share of citations whose passage actually supports the attached claim | The core trust metric |
| Faithfulness | Share of report claims with no supporting evidence in the stored passages | Direct measure of hallucination |
| Claim coverage | Share of the gold key facts that appear in the report | Measures completeness |
| Conflict handling | On conflict questions, whether the report mentions the disagreement | Tests honesty about uncertainty |
| Abstention | On unanswerable questions, whether the report says evidence was insufficient | Tests that the system does not bluff |
| Cost per report | Dollars from the event log | Practical viability |
| Latency per report | Seconds from start to completion | Practical viability |
| Arabic parity | Each metric for Arabic questions compared with English ones | Shows the Arabic support is real |

### Judging method

Support and coverage checks use an LLM judge with a fixed rubric. To keep that honest, label about 20% of the outputs by hand and report how often the judge agrees with you (for example with Cohen's kappa). If agreement is low, fix the rubric before trusting any numbers.

### Ablation: the key results table

Run the same benchmark under four configurations and fill every metric for each one.

| Configuration | What it isolates |
| --- | --- |
| A. Single LLM, no search | The floor: how much a plain model hallucinates on these questions |
| B. Single LLM with one search step | The value of search alone |
| C. Multi-agent without the Critic | The value of planning and parallel research |
| D. Full system with the Critic loop | The value of verification; the headline comparison is D against C |

Two further experiments are cheap and informative: swap the Critic to the same model as the Writer to test whether model independence matters, and vary the Critic round limit (0, 1, 2, 3) to find where extra rounds stop paying for their cost.

### Running it

A command such as `make eval` runs the benchmark, writes a results file, and renders the ablation table. GitHub Actions runs a 5-question subset on every pull request to catch regressions, and the full benchmark runs on demand before releases.

## 11. Deployment, observability, cost control and security

The goal is a public demo that stays cheap, cannot be abused, and lets you debug any run after it finishes.

### Deployment

- **Local development:** Docker Compose starts the API, worker, Postgres, Redis, Qdrant and the frontend with one command.
- **Production option 1 (simple):** one small virtual server (for example a Hetzner or DigitalOcean machine) running the same Compose file behind a reverse proxy such as Caddy, which handles HTTPS automatically.
- **Production option 2 (managed):** frontend on Vercel or Cloudflare Pages; API and worker on Fly.io, Railway or Render; managed Postgres; Redis and Qdrant as managed or container services.
- **Secrets:** API keys live in environment variables or the platform's secret store, never in the repository.

### Observability

- Every agent step is an event in Postgres (the product trace), and every LLM call is also sent to Langfuse (the engineering trace with prompts, outputs, token counts and cost).
- Logs are structured JSON tagged with `run_id`, so one run's logs can be filtered in seconds.
- A small dashboard tracks runs per day, failure rate, average cost, and average latency per stage.

### Cost control

| Lever | How it works |
| --- | --- |
| Per-run budget | Orchestrator stops starting new steps when spend, tokens or time would exceed the cap |
| Model tiering | Cheap model for extraction and query writing; strong model only for planning, writing and verification |
| Caching | Search results and fetched pages cached by URL and content hash; repeat runs cost far less |
| Early stopping | Stop the critic loop when a round finds no new problems |
| Passage limits | Cap passages sent to each LLM call using reranking, instead of sending whole pages |
| Demo guardrails | Public demo uses a daily spending cap and a rate limit per visitor |

### Security and safety

- **Prompt injection:** web pages can contain text that tries to command the agent. Fetched content is always placed in delimited data blocks, agents are told it is untrusted, and agents have no tools that can take harmful actions (no email, no file writes, no shell).
- **SSRF protection:** the fetch tool blocks private IP ranges, localhost and cloud metadata addresses, so a crafted URL cannot reach internal services.
- **Respect for sites:** the fetcher honors robots.txt, identifies itself in the user agent, and rate-limits per domain.
- **Authentication:** version 1 can start with simple email login using JWTs; add per-user rate limits before any public launch.
- **Privacy:** questions and reports are private to the user; provide a delete-run action that removes the run's rows and vectors.
- **Output honesty:** reports always list sources and a Risks section; the UI labels the report as AI-generated and verified against sources, not guaranteed correct.

## 12. Risks, hard problems and mitigations

These are the places where the project is most likely to stall, with the plan for each. Solving them is also where most of the learning and most of the interview material comes from.

| Risk or hard problem | Why it happens | Mitigation |
| --- | --- | --- |
| Hallucinated or mismatched citations | The Writer cites a source that does not really support the claim | Writer may cite only supplied evidence ids; the Critic verifies against the stored passage; evaluation tracks citation precision |
| Context too large | Pages and many passages do not fit in one prompt | Passage-level storage, reranking, and sending each agent only the passages it needs |
| Endless loops | The Critic keeps finding small issues, the Researchers keep searching | Hard round limit, budget cap, and a stop rule when a round adds no new supported claims |
| Parallel failures | A page times out, a model returns invalid JSON, a rate limit hits | Timeouts, bounded retries with backoff, one JSON repair attempt, and skipping a failed step with a logged event |
| API cost during development | Many test runs with strong models | Record-and-replay mode, a cheap model for iteration, strict per-run budgets, cached pages |
| Fragile web fetching | Sites block bots, use JavaScript, or change layout | Search API first, trafilatura for extraction, Playwright only as fallback, a clear "could not fetch" event |
| Arabic text problems | Mixed scripts, diacritics, and right-to-left display | Unicode normalization before embedding, a multilingual embedding model, RTL tests in the UI, Arabic questions in the benchmark |
| Judge unreliability | An LLM judge may score inconsistently | Hand-label a sample, report agreement, fix the rubric, use a fixed judge model and prompt version |
| Scope creep | The UI and extra features swallow the time | Build the baseline plus Critic loop before any UI polish; features beyond section 8 wait until after week 7 |
| Prompt injection from web pages | Fetched text may contain instructions | Delimited untrusted data blocks, no harmful tools, output validation |

### Decisions to make early

- **LangGraph or a custom loop.** Recommended: write the first working version as plain Python functions that take and return typed state. Wrap them in LangGraph once the flow is stable, so you learn the mechanics first and still get checkpointing and pause-and-resume.
- **Search provider.** Pick one (Tavily or Brave) behind a `SearchTool` interface so switching later is a one-file change.
- **Primary model providers.** One provider for Planner, Researcher and Writer, a different one for the Critic.

## 13. Roadmap and project structure

The plan assumes about 8 weeks of part-time work. Each week ends with something that runs, so you always have a demonstrable project even if time gets short.

| Week | Focus | Deliverable | Done when |
| --- | --- | --- | --- |
| 1 | Foundations and single-agent baseline | Repo, Docker Compose, Postgres schema, event logging, one agent that searches and answers with citations | A question returns a cited answer and every step appears in the events table |
| 2 | Retrieval layer | Page fetching and extraction, passage chunking, embeddings, Qdrant storage, reranking | Evidence rows with exact offsets are stored and searchable per run |
| 3 | Planner and parallel Researchers | Plan schema, sub-question fan-out, concurrency limits, caching | A plan produces evidence for every sub-question in parallel |
| 4 | Writer with atomic claims | Claim extraction pass, prose pass, citation validation | Every citation in a report points to a real stored evidence id |
| 5 | Critic loop and orchestrator | Verdicts, gap detection, round limits, budget enforcement, stop rules | The loop drops or rewrites bad claims and ends on its own |
| 6 | API, streaming and UI | SSE endpoint, plan approval, trace viewer, report page with citation panel | A full run is watchable in the browser end to end |
| 7 | Evaluation | Benchmark, record-and-replay, judge, ablation table | `make eval` produces the A, B, C, D comparison |
| 8 | Release | Deployment, demo video, README, tradeoffs write-up, polish | Public URL works and the repository is recruiter-ready |

### Suggested repository layout

```text
atlas-research/
  backend/
    app/
      api/            # FastAPI routes, SSE endpoint
      agents/         # planner.py, researcher.py, writer.py, critic.py
      orchestrator/   # state machine, budgets, stop rules
      tools/          # search.py, fetch.py, extract.py, vector.py
      llm/            # LLMClient interface and provider adapters
      models/         # Pydantic schemas and SQLAlchemy models
      prompts/        # versioned prompt files
      worker.py       # Arq worker entry point
    migrations/       # Alembic
    tests/
  frontend/
    src/
      pages/          # NewRun, PlanReview, LiveRun, Report, History
      components/     # AgentGraph, Timeline, BudgetMeter, CitationPanel
      hooks/          # useRunEvents (SSE)
  eval/
    benchmark/        # questions.jsonl, gold facts, recorded web snapshots
    judges/           # rubric prompts
    run_eval.py
  docs/               # architecture, ADRs, results
  docker-compose.yml
  Makefile
  README.md
```

### Definition of success

- The full system beats configuration C (no Critic) on faithfulness and citation precision in your own benchmark, with numbers in the README.
- A stranger can open the demo URL, ask a question, and watch a cited report appear in a few minutes.
- The repository explains its design decisions, including what did not work.
