"""Claude calls used by the graph, behind a small injectable interface."""

import time
from typing import Protocol

import anthropic
from anthropic.lib._parse._transform import to_json_output_format
from langsmith import traceable
from pydantic import BaseModel, ValidationError

from app.agent.prompts import (
    EXTRACT_SYSTEM,
    JUDGE_SYSTEM,
    build_extract_messages,
    build_judge_messages,
)
from app.agent.schemas import Extraction, ImageInput, Verdict
from app.config import Settings
from app.logging import get_logger

log = get_logger(__name__)

MAX_TOKENS = 16000
# Server-side fallback: on a refusal (e.g. a phishing screenshot misread as "cyber"
# content) the API retries the same request on another model within the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def supports_server_fallback(model: str) -> bool:
    """Haiku has no server-side fallback (a list of fallbacks even returns a 400)."""
    return not model.startswith("claude-haiku")


class LLMRefusalError(Exception):
    def __init__(self, category: str | None):
        super().__init__(f"model refused (category={category})")
        self.category = category


class LLMOutputError(Exception):
    pass


class AnalysisLLM(Protocol):
    async def extract(self, caption: str | None, images: list[ImageInput]) -> Extraction: ...

    async def judge(self, extraction: Extraction, caption: str | None) -> Verdict: ...


class AnthropicAnalysisLLM:
    def __init__(self, client: anthropic.AsyncAnthropic, model: str):
        self._client = client
        self._model = model

    @traceable(run_type="llm", name="claude.extract")
    async def extract(self, caption: str | None, images: list[ImageInput]) -> Extraction:
        return await self._call(
            node="extract",
            system=EXTRACT_SYSTEM,
            messages=build_extract_messages(caption, images),
            schema=Extraction,
            effort="low",
        )

    @traceable(run_type="llm", name="claude.judge")
    async def judge(self, extraction: Extraction, caption: str | None) -> Verdict:
        return await self._call(
            node="judge",
            system=JUDGE_SYSTEM,
            messages=build_judge_messages(extraction, caption),
            schema=Verdict,
            effort="medium",
        )

    async def _call[T: BaseModel](
        self, *, node: str, system: str, messages: list[dict], schema: type[T], effort: str
    ) -> T:
        # No temperature and no forced tool_choice: Claude Sonnet 5.5 rejects both with a 400.
        # messages.create + our own parsing instead of messages.parse: parse() validates every
        # text block, so a partial answer from a model that declined before a fallback (or a
        # refusal after some text) would raise before we can inspect stop_reason.
        fallback = (
            {"betas": [FALLBACK_BETA], "fallbacks": "default"}
            if supports_server_fallback(self._model)
            else {}
        )
        start = time.perf_counter()
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_config={"effort": effort, "format": to_json_output_format(schema)},
            **fallback,
        )
        log.info(
            "llm.call.completed",
            node=node,
            model=self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason,
            ms=round((time.perf_counter() - start) * 1000),
        )
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            log.warning("llm.refusal", node=node, category=category)
            raise LLMRefusalError(category)
        return _parse_output(response, schema, node)


def _parse_output[T: BaseModel](response, schema: type[T], node: str) -> T:
    """Validate the JSON written by the model that finished the turn.

    A `fallback` block marks where a declining model's output ends and the fallback
    model's begins: only the text after the last one is the answer.
    """
    blocks = list(response.content)
    last_fallback = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    text = "".join(b.text for b in blocks[last_fallback + 1 :] if b.type == "text")
    if not text:
        raise LLMOutputError(f"{node}: empty output (stop_reason={response.stop_reason})")
    try:
        return schema.model_validate_json(text)
    except ValidationError:
        # No `from e`: the validation error embeds the model output (user content).
        raise LLMOutputError(
            f"{node}: output does not match {schema.__name__} "
            f"(stop_reason={response.stop_reason})"
        ) from None


def create_llm(settings: Settings) -> AnthropicAnalysisLLM:
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=2, timeout=60
    )
    return AnthropicAnalysisLLM(client, settings.anthropic_model)
