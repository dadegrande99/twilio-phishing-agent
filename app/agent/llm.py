"""Claude calls used by the graph, behind a small injectable interface."""

import time
from typing import Protocol

import anthropic
from langsmith import traceable
from pydantic import BaseModel

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
        start = time.perf_counter()
        response = await self._client.beta.messages.parse(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
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
        if response.parsed_output is None:
            raise LLMOutputError(f"{node}: no parsed output (stop_reason={response.stop_reason})")
        return response.parsed_output


def create_llm(settings: Settings) -> AnthropicAnalysisLLM:
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value(), max_retries=2, timeout=60
    )
    return AnthropicAnalysisLLM(client, settings.anthropic_model)
