"""Tests for the LLMClient interface, FakeLLMClient and Anthropic adapter.

Only fakes are used here: no network, no API key, no money spent.
"""

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from app.llm import (
    AnthropicLLMClient,
    FakeLLMClient,
    LLMNotSupportedError,
    LLMOutputError,
    LLMRefusalError,
)
from app.llm.pricing import cost_usd


class Answer(BaseModel):
    answer: str
    evidence_ids: list[str]


# ---------- pricing ----------


def test_cost_is_computed_per_million_tokens() -> None:
    # claude-opus-5-5: $4 in / $20 out per 1M tokens
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == Decimal("24")
    assert cost_usd("claude-opus-5-5", 1_000, 500) == Decimal("0.014")


def test_unknown_model_has_no_price() -> None:
    with pytest.raises(KeyError):
        cost_usd("some-other-model", 1, 1)


# ---------- FakeLLMClient ----------


async def test_fake_generate_returns_scripted_text_and_records_call() -> None:
    fake = FakeLLMClient(texts=["hello"])
    result = await fake.generate(system="sys", prompt="hi")
    assert result.text == "hello"
    assert fake.calls[0].prompt == "hi" and fake.calls[0].system == "sys"


async def test_fake_structured_validates_dicts_into_the_schema() -> None:
    fake = FakeLLMClient(structured=[{"answer": "42", "evidence_ids": ["e1"]}])
    result = await fake.generate_structured(system="s", prompt="p", schema=Answer)
    assert result.parsed == Answer(answer="42", evidence_ids=["e1"])


async def test_fake_structured_rejects_output_that_breaks_the_schema() -> None:
    fake = FakeLLMClient(structured=[{"answer": "42"}])  # evidence_ids missing
    with pytest.raises(LLMOutputError):
        await fake.generate_structured(system="s", prompt="p", schema=Answer)


async def test_fake_fails_loudly_when_the_script_runs_out() -> None:
    fake = FakeLLMClient()
    with pytest.raises(AssertionError, match="no scripted"):
        await fake.generate(system="s", prompt="p")


async def test_fake_embed_gives_stable_vectors() -> None:
    fake = FakeLLMClient()
    first = await fake.embed(["a", "b"])
    assert len(first) == 2 and first == await fake.embed(["a", "b"])
    assert first[0] != first[1]


# ---------- Anthropic adapter (with a fake SDK client) ----------


def sdk_response(
    text: str = "ok",
    parsed: Any = None,
    stop_reason: str = "end_turn",
    tokens_in: int = 1000,
    tokens_out: int = 500,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out),
    )


def schema_error() -> ValidationError:
    try:
        Answer.model_validate({"answer": "x"})
    except ValidationError as err:
        return err
    raise AssertionError("expected a validation error")


class FakeSDK:
    """Stands in for anthropic.AsyncAnthropic. Each call pops the next scripted item."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._call, parse=self._call)

    async def _call(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def adapter(sdk: FakeSDK) -> AnthropicLLMClient:
    return AnthropicLLMClient(model="claude-opus-5-5", client=sdk)  # type: ignore[arg-type]


async def test_generate_returns_text_usage_and_cost() -> None:
    sdk = FakeSDK([sdk_response(text="Paris", tokens_in=1000, tokens_out=500)])
    result = await adapter(sdk).generate(system="sys", prompt="Capital of France?")
    assert result.text == "Paris"
    assert (result.usage.tokens_in, result.usage.tokens_out) == (1000, 500)
    assert result.usage.cost_usd == Decimal("0.014")
    assert result.usage.model == "claude-opus-5-5"
    assert sdk.calls[0]["model"] == "claude-opus-5-5"
    assert sdk.calls[0]["system"] == "sys"


async def test_structured_returns_validated_model() -> None:
    parsed = Answer(answer="A", evidence_ids=["e1"])
    sdk = FakeSDK([sdk_response(parsed=parsed)])
    result = await adapter(sdk).generate_structured(system="s", prompt="p", schema=Answer)
    assert result.parsed == parsed
    assert sdk.calls[0]["output_format"] is Answer


async def test_structured_gets_one_repair_attempt_and_usage_adds_up() -> None:
    good = Answer(answer="A", evidence_ids=["e1"])
    sdk = FakeSDK([schema_error(), sdk_response(parsed=good, tokens_in=2000, tokens_out=100)])
    result = await adapter(sdk).generate_structured(system="s", prompt="p", schema=Answer)
    assert result.parsed == good
    assert len(sdk.calls) == 2
    repair_prompt = str(sdk.calls[1]["messages"])
    assert "evidence_ids" in repair_prompt  # the validation error is shown to the model
    # Only the successful attempt reports usage: the SDK raises before returning it
    # for the failed one, so that attempt's tokens are not counted.
    assert result.usage.tokens_in == 2000


async def test_structured_gives_up_after_one_repair() -> None:
    sdk = FakeSDK([schema_error(), schema_error()])
    with pytest.raises(LLMOutputError):
        await adapter(sdk).generate_structured(system="s", prompt="p", schema=Answer)
    assert len(sdk.calls) == 2


async def test_refusal_raises_a_clear_error() -> None:
    sdk = FakeSDK([sdk_response(stop_reason="refusal")])
    with pytest.raises(LLMRefusalError):
        await adapter(sdk).generate(system="s", prompt="p")


async def test_truncated_output_is_an_error_not_a_silent_partial() -> None:
    sdk = FakeSDK([sdk_response(stop_reason="max_tokens")])
    with pytest.raises(LLMOutputError):
        await adapter(sdk).generate(system="s", prompt="p")


async def test_anthropic_has_no_embeddings() -> None:
    with pytest.raises(LLMNotSupportedError):
        await adapter(FakeSDK([])).embed(["x"])


# ---------- rule 3: provider SDKs only inside app/llm ----------


def test_no_provider_sdk_is_imported_outside_app_llm() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        if "llm" in path.relative_to(app_dir).parts[:1]:
            continue
        text = path.read_text(encoding="utf-8")
        for provider in ("anthropic", "openai"):
            if f"import {provider}" in text or f"from {provider}" in text:
                offenders.append(f"{path.name}: {provider}")
    assert offenders == []
