"""A scripted LLMClient for tests. Never touches the network."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.base import LLMResult, LLMUsage, StructuredResult
from app.llm.errors import LLMOutputError

_FAKE_USAGE = LLMUsage(model="fake", tokens_in=0, tokens_out=0, cost_usd=Decimal(0))


@dataclass(frozen=True)
class FakeCall:
    system: str
    prompt: str
    schema: type[BaseModel] | None = None


class FakeLLMClient:
    """Pops the next scripted reply on each call and records what it was asked."""

    def __init__(
        self,
        *,
        texts: Sequence[str] = (),
        structured: Sequence[BaseModel | dict[str, Any]] = (),
        usage: LLMUsage = _FAKE_USAGE,
    ) -> None:
        self._texts = list(texts)
        self._structured = list(structured)
        self._usage = usage
        self.calls: list[FakeCall] = []

    async def generate(self, *, system: str, prompt: str, max_tokens: int = 16000) -> LLMResult:
        self.calls.append(FakeCall(system, prompt))
        assert self._texts, "FakeLLMClient has no scripted text left for generate()"
        return LLMResult(text=self._texts.pop(0), usage=self._usage)

    async def generate_structured[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], max_tokens: int = 16000
    ) -> StructuredResult[T]:
        self.calls.append(FakeCall(system, prompt, schema))
        assert self._structured, "FakeLLMClient has no scripted structured reply left"
        item = self._structured.pop(0)
        data = item.model_dump() if isinstance(item, BaseModel) else item
        try:
            parsed = schema.model_validate(data)
        except ValidationError as err:
            raise LLMOutputError(f"Scripted reply does not match {schema.__name__}: {err}") from err
        return StructuredResult(parsed=parsed, usage=self._usage)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Deterministic 8-number vectors derived from a hash of each text."""
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]
