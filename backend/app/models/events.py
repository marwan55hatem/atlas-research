from enum import StrEnum


class Agent(StrEnum):
    ORCHESTRATOR = "orchestrator"
    PLANNER = "planner"
    RESEARCHER = "researcher"
    WRITER = "writer"
    CRITIC = "critic"
    BASELINE = "baseline"


class EventType(StrEnum):
    """Event types from section 5 of docs/architecture.md."""

    RUN_STARTED = "run_started"
    PLAN_CREATED = "plan_created"
    PLAN_APPROVED = "plan_approved"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    EVIDENCE_STORED = "evidence_stored"
    CLAIM_DRAFTED = "claim_drafted"
    VERDICT = "verdict"
    GAP_FOUND = "gap_found"
    REPORT_VERSION = "report_version"
    BUDGET_WARNING = "budget_warning"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
