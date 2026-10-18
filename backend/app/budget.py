"""Per-run budget enforcement for dollars, tokens and seconds (rule 6).

The Orchestrator calls `check` before every step and `record` after it.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.models import Run

WARNING_FRACTION = 0.8


@dataclass(frozen=True)
class BudgetLimits:
    usd: Decimal
    tokens: int
    seconds: float


class BudgetExceeded(Exception):
    """Raised when a step would push a run past one of its limits."""

    def __init__(self, resource: str, limit: Decimal | float, would_be: Decimal | float) -> None:
        self.resource = resource  # "usd", "tokens" or "seconds"
        self.limit = limit
        self.would_be = would_be
        super().__init__(f"Budget exceeded for {resource}: {would_be} would pass the limit {limit}")


def _to_decimal(value: Decimal | float) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class BudgetTracker:
    def __init__(
        self,
        limits: BudgetLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
        spent_usd: Decimal | float = Decimal(0),
        spent_tokens: int = 0,
        elapsed_before: float = 0.0,
    ) -> None:
        self.limits = limits
        self.spent_usd = _to_decimal(spent_usd)
        self.spent_tokens = spent_tokens
        self._clock = clock
        self._started = clock()
        self._elapsed_before = elapsed_before
        self._warned = False

    @classmethod
    def for_run(cls, run: Run, *, clock: Callable[[], float] = time.monotonic) -> "BudgetTracker":
        """Build a tracker from a runs row, continuing from what it already spent."""
        limits = BudgetLimits(
            usd=_to_decimal(run.budget_usd),
            tokens=run.budget_tokens,
            seconds=run.budget_seconds,
        )
        return cls(
            limits,
            clock=clock,
            spent_usd=run.spent_usd or Decimal(0),
            spent_tokens=run.spent_tokens or 0,
        )

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed_before + (self._clock() - self._started)

    def record(self, *, tokens: int, cost_usd: Decimal | float) -> None:
        cost = _to_decimal(cost_usd)
        if tokens < 0 or cost < 0:
            raise ValueError("Usage cannot be negative")
        self.spent_tokens += tokens
        self.spent_usd += cost

    def check(
        self,
        *,
        next_tokens: int = 0,
        next_cost_usd: Decimal | float = Decimal(0),
        next_seconds: float = 0.0,
    ) -> None:
        """Raise BudgetExceeded if the next step (by estimate) would break a limit.

        Landing exactly on a limit is allowed; going past it is not.
        """
        next_cost = _to_decimal(next_cost_usd)
        if next_tokens < 0 or next_cost < 0 or next_seconds < 0:
            raise ValueError("Estimates cannot be negative")

        tokens = self.spent_tokens + next_tokens
        if tokens > self.limits.tokens:
            raise BudgetExceeded("tokens", self.limits.tokens, tokens)
        usd = self.spent_usd + next_cost
        if usd > self.limits.usd:
            raise BudgetExceeded("usd", self.limits.usd, usd)
        seconds = self.elapsed_seconds + next_seconds
        if seconds > self.limits.seconds:
            raise BudgetExceeded("seconds", self.limits.seconds, seconds)

    def fraction_used(self) -> float:
        """Share of the most-consumed limit, from 0.0 to beyond 1.0 if overspent."""
        return max(
            self.spent_tokens / self.limits.tokens,
            float(self.spent_usd / self.limits.usd),
            self.elapsed_seconds / self.limits.seconds,
        )

    def should_warn(self) -> bool:
        """True once, the first time usage reaches 80% of any limit."""
        if not self._warned and self.fraction_used() >= WARNING_FRACTION:
            self._warned = True
            return True
        return False
