from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db import make_session_factory
from app.events import EventLogger
from app.models import Base, Run
from app.models.events import Agent, EventType


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with make_session_factory(engine)() as session:
        yield session
    await engine.dispose()


async def make_run(session: AsyncSession) -> Run:
    run = Run(question="Q", budget_usd=1, budget_tokens=1000, budget_seconds=60)
    session.add(run)
    await session.commit()
    return run


async def test_seq_starts_at_one_and_increases(session: AsyncSession) -> None:
    run = await make_run(session)
    logger = EventLogger(session, run.id)
    first = await logger.log(Agent.ORCHESTRATOR, EventType.RUN_STARTED)
    second = await logger.log(Agent.BASELINE, EventType.LLM_CALL)
    third = await logger.log(Agent.BASELINE, EventType.RUN_COMPLETED)
    assert [first.seq, second.seq, third.seq] == [1, 2, 3]


async def test_seq_is_independent_per_run(session: AsyncSession) -> None:
    run_a, run_b = await make_run(session), await make_run(session)
    await EventLogger(session, run_a.id).log(Agent.ORCHESTRATOR, EventType.RUN_STARTED)
    await EventLogger(session, run_a.id).log(Agent.BASELINE, EventType.LLM_CALL)
    event_b = await EventLogger(session, run_b.id).log(Agent.ORCHESTRATOR, EventType.RUN_STARTED)
    assert event_b.seq == 1


async def test_event_stores_payload_and_usage(session: AsyncSession) -> None:
    run = await make_run(session)
    event = await EventLogger(session, run.id).log(
        Agent.BASELINE,
        EventType.TOOL_CALL,
        {"tool": "web_search", "args": {"query": "x"}},
        tokens_in=1850,
        tokens_out=240,
        cost_usd=Decimal("0.0031"),
        latency_ms=1480,
    )
    assert event.payload["tool"] == "web_search"
    assert (event.tokens_in, event.tokens_out, event.latency_ms) == (1850, 240, 1480)
    assert event.cost_usd == Decimal("0.0031")
    assert event.ts is not None


async def test_event_is_committed_so_other_sessions_see_it(session: AsyncSession) -> None:
    run = await make_run(session)
    await EventLogger(session, run.id).log(Agent.ORCHESTRATOR, EventType.RUN_STARTED)
    await session.rollback()  # a later failure must not erase the trace
    assert (await EventLogger(session, run.id).log(Agent.BASELINE, EventType.LLM_CALL)).seq == 2


async def test_unknown_agent_or_type_is_rejected(session: AsyncSession) -> None:
    run = await make_run(session)
    logger = EventLogger(session, run.id)
    with pytest.raises(ValueError):
        await logger.log("hacker", EventType.RUN_STARTED)
    with pytest.raises(ValueError):
        await logger.log(Agent.BASELINE, "made_up_type")
