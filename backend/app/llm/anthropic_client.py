"""LLMClient adapter for Claude. This is the only module that imports the Anthropic SDK."""

from collections.abc import Sequence

import anthropic
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMResult, LLMUsage, StructuredResult
from app.llm.errors import LLMNotSupportedError, LLMOutputError, LLMRefusalError
from app.llm.pricing import cost_usd


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        # api_key=None lets the SDK fall back to ANTHROPIC_API_KEY in the environment.
        self._sdk = client or anthropic.AsyncAnthropic(api_key=api_key)

    def _usage(self, response: anthropic.types.Message) -> LLMUsage:
        tokens_in, tokens_out = response.usage.input_tokens, response.usage.output_tokens
        return LLMUsage(
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd(self.model, tokens_in, tokens_out),
        )

    def _check_finished(self, response: anthropic.types.Message) -> None:
        if response.stop_reason == "refusal":
            raise LLMRefusalError("The model declined to answer this request")
        if response.stop_reason == "max_tokens":
            raise LLMOutputError("The model's answer was cut off at max_tokens")

    async def generate(self, *, system: str, prompt: str, max_tokens: int = 16000) -> LLMResult:
        response = await self._sdk.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        self._check_finished(response)
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(text=text, usage=self._usage(response))

    async def generate_structured[T: BaseModel](
        self, *, system: str, prompt: str, schema: type[T], max_tokens: int = 16000
    ) -> StructuredResult[T]:
        """Ask for output matching `schema`; on invalid output, one repair attempt, then fail."""
        messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]
        last_error: ValidationError | None = None
        for _attempt in range(2):
            try:
                response = await self._sdk.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                    output_format=schema,
                )
            except ValidationError as err:
                last_error = err
                messages = [
                    {"role": "user", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply did not match the required JSON schema:\n"
                            f"{err}\nReply again with JSON that fixes these problems."
                        ),
                    },
                ]
                continue
            self._check_finished(response)
            parsed = response.parsed_output
            if parsed is None:
                raise LLMOutputError("The model returned no structured output")
            return StructuredResult(parsed=parsed, usage=self._usage(response))
        raise LLMOutputError(f"Output still invalid after one repair attempt: {last_error}")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise LLMNotSupportedError(
            "Anthropic has no embeddings API; embeddings come from a separate model (week 2)"
        )
