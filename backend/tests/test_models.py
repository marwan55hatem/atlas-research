import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, Event, Run

EXPECTED_TABLES = {
    "runs",
    "sub_questions",
    "sources",
    "evidence",
    "claims",
    "claim_evidence",
    "verdicts",
    "reports",
    "events",
}


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_all_nine_tables_are_defined(engine) -> None:  # type: ignore[no-untyped-def]
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_run_stores_budget_and_defaults(engine) -> None:  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        run = Run(question="What is X?", budget_usd=1.5, budget_tokens=100_000, budget_seconds=300)
        session.add(run)
        session.commit()
        assert run.status == "queued"
        assert run.spent_tokens == 0
        assert isinstance(run.id, uuid.UUID)


def test_event_seq_is_unique_per_run(engine) -> None:  # type: ignore[no-untyped-def]
    with Session(engine) as session:
        run = Run(question="Q", budget_usd=1, budget_tokens=1000, budget_seconds=60)
        session.add(run)
        session.flush()
        session.add(Event(run_id=run.id, seq=1, agent="orchestrator", type="run_started"))
        session.commit()
        session.add(Event(run_id=run.id, seq=1, agent="orchestrator", type="llm_call"))
        with pytest.raises(IntegrityError):
            session.commit()
