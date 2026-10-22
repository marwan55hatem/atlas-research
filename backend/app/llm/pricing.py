"""Prices in USD per 1M tokens (input, output). Cached from the Anthropic price list 2026-10-06.

Cache reads/writes and long-prompt tiers are not modelled yet; costs may be slightly
off for those cases.
"""

from decimal import Decimal

PRICES_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5-5": (Decimal("4.00"), Decimal("20.00")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
}


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """Raises KeyError for a model without a known price, so spend is never silently zero."""
    price_in, price_out = PRICES_PER_MILLION[model]
    return (price_in * tokens_in + price_out * tokens_out) / Decimal(1_000_000)
