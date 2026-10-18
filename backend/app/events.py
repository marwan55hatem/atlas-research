"""Event logger: writes every agent step to the append-only events table (rule 5)."""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, Run
from app.models.events import Agent, EventType


class EventLogger:
    """Appends events for one run.

    Each call commits immediately, so the trace survives a later failure. That also
    commits anything else pending on the session, so keep trace writes and business
    writes in the order you want them saved.
    """

    def __init__(self, session: AsyncSession, run_id: uuid.UUID) -> None:
        self._session = session
        self._run_id = run_id

    async def log(
        self,
        agent: Agent | str,
        type: EventType | str,
        payload: dict[str, Any] | None = None,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: Decimal | float | None = None,
        latency_ms: int | None = None,
    ) -> Event:
        agent = Agent(agent)  # raises ValueError for unknown names
        type = EventType(type)

        # Lock the run row so two writers cannot hand out the same seq. The lock is
        # released by the commit below. (SQLite ignores FOR UPDATE; Postgres honours it.)
        await self._session.execute(select(Run.id).where(Run.id == self._run_id).with_for_update())
        last_seq = await self._session.scalar(
            select(func.max(Event.seq)).where(Event.run_id == self._run_id)
        )

        event = Event(
            run_id=self._run_id,
            seq=(last_seq or 0) + 1,
            agent=agent.value,
            type=type.value,
            payload=payload or {},
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=None if cost_usd is None else Decimal(str(cost_usd)),
            latency_ms=latency_ms,
        )
        self._session.add(event)
        await self._session.commit()
        return event
