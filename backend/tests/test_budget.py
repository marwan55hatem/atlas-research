from decimal import Decimal

import pytest

from app.budget import BudgetExceeded, BudgetLimits, BudgetTracker
from app.models import Run


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_tracker(clock: FakeClock | None = None, **overrides: object) -> BudgetTracker:
    limits = BudgetLimits(usd=Decimal("1.50"), tokens=10_000, seconds=300)
    return BudgetTracker(limits, clock=clock or FakeClock(), **overrides)  # type: ignore[arg-type]


def test_fresh_tracker_allows_a_step() -> None:
    make_tracker().check(next_tokens=500, next_cost_usd=Decimal("0.10"))


def test_step_that_would_break_token_limit_is_refused() -> None:
    tracker = make_tracker()
    tracker.record(tokens=9_800, cost_usd=Decimal("0.10"))
    with pytest.raises(BudgetExceeded) as err:
        tracker.check(next_tokens=300)
    assert err.value.resource == "tokens"


def test_step_that_would_break_dollar_limit_is_refused() -> None:
    tracker = make_tracker()
    tracker.record(tokens=100, cost_usd=Decimal("1.40"))
    with pytest.raises(BudgetExceeded) as err:
        tracker.check(next_cost_usd=Decimal("0.20"))
    assert err.value.resource == "usd"


def test_exactly_reaching_the_limit_is_allowed() -> None:
    tracker = make_tracker()
    tracker.record(tokens=9_000, cost_usd=Decimal("1.00"))
    tracker.check(next_tokens=1_000, next_cost_usd=Decimal("0.50"))


def test_time_limit_uses_elapsed_wall_clock() -> None:
    clock = FakeClock()
    tracker = make_tracker(clock)
    clock.now += 290
    tracker.check(next_seconds=10)  # lands exactly on 300: allowed
    with pytest.raises(BudgetExceeded) as err:
        tracker.check(next_seconds=11)
    assert err.value.resource == "seconds"


def test_already_out_of_time_refuses_even_a_free_step() -> None:
    clock = FakeClock()
    tracker = make_tracker(clock)
    clock.now += 301
    with pytest.raises(BudgetExceeded):
        tracker.check()


def test_record_accumulates() -> None:
    tracker = make_tracker()
    tracker.record(tokens=100, cost_usd=Decimal("0.01"))
    tracker.record(tokens=250, cost_usd=0.02)  # floats are accepted
    assert tracker.spent_tokens == 350
    assert tracker.spent_usd == Decimal("0.03")


def test_negative_amounts_are_rejected() -> None:
    tracker = make_tracker()
    with pytest.raises(ValueError):
        tracker.record(tokens=-1, cost_usd=Decimal(0))
    with pytest.raises(ValueError):
        tracker.check(next_tokens=-5)


def test_fraction_used_reports_the_most_consumed_resource() -> None:
    clock = FakeClock()
    tracker = make_tracker(clock)
    tracker.record(tokens=2_000, cost_usd=Decimal("1.20"))  # 20% tokens, 80% dollars
    clock.now += 30  # 10% time
    assert tracker.fraction_used() == pytest.approx(0.8)


def test_warning_fires_once_when_80_percent_is_crossed() -> None:
    tracker = make_tracker()
    tracker.record(tokens=7_000, cost_usd=Decimal("0.10"))
    assert tracker.should_warn() is False
    tracker.record(tokens=1_500, cost_usd=Decimal("0.10"))  # 85% of tokens
    assert tracker.should_warn() is True
    assert tracker.should_warn() is False


def test_tracker_resumes_from_a_run_row() -> None:
    run = Run(
        question="Q",
        budget_usd=Decimal("2"),
        budget_tokens=5_000,
        budget_seconds=120,
        spent_usd=Decimal("0.50"),
        spent_tokens=1_000,
    )
    tracker = BudgetTracker.for_run(run, clock=FakeClock())
    assert tracker.limits == BudgetLimits(usd=Decimal("2"), tokens=5_000, seconds=120)
    assert (tracker.spent_usd, tracker.spent_tokens) == (Decimal("0.50"), 1_000)
