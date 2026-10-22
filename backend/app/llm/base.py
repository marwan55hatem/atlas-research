"""The LLMClient interface (CLAUDE.md rule 3).

Agents and tools talk to this interface only. Provider SDKs are imported in the
adapter modules inside app/llm/ and nowhere else.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class LLMUsage:
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal


@dataclass(frozen=True)
class LLMResult:
    text: str
    usage: LLMUsage


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    parsed: T
    usage: LLMUsage


class LLMClient(Protocol):
    """One model, one provider. Use one client per model tier."""

    async def generate(self, *, system: str, prompt: str, max_tokens: int = 16000) -> LLMResult: ...

    async def generate_structured[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], max_tokens: int = 16000
    ) -> StructuredResult[T]:
        """Return output validated by `schema` (rule 1). Raises LLMOutputError if invalid."""
        ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...
