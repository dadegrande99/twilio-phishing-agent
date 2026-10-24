"""Runs one analysis per inbound WhatsApp message, after the webhook has answered.

Order of messages to the user: ack → progress (after extraction) → final verdict.
Analyses of the same user run one after the other (per-number lock).
"""

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from uuid import uuid4

import structlog

from app.agent.formatting import (
    ACK_TEXT,
    GENERIC_ERROR_TEXT,
    HELP_TEXT,
    REFUSAL_TEXT,
    TOO_LARGE_TEXT,
    UNSUPPORTED_MEDIA_TEXT,
)
from app.agent.graph import run_analysis
from app.agent.llm import LLMRefusalError
from app.channels.models import InboundMessage, MessagingGateway
from app.config import Settings
from app.logging import get_logger, mask_number
from app.media import ALLOWED_IMAGE_TYPES, MediaRejected, normalize_content_type
from app.whitelist import AllowedUser

log = get_logger(__name__)

_REJECT_TEXTS = {
    "too_large": TOO_LARGE_TEXT,
    "unsupported_type": UNSUPPORTED_MEDIA_TEXT,
    "download_failed": GENERIC_ERROR_TEXT,
}


class MessageDeduper:
    """Remembers recently seen MessageSids so a retried webhook is processed once."""

    def __init__(self, ttl_seconds: float = 600, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}

    def first_time(self, message_sid: str) -> bool:
        now = self._clock()
        self._seen = {sid: t for sid, t in self._seen.items() if now - t <= self._ttl}
        if message_sid in self._seen:
            return False
        self._seen[message_sid] = now
        return True


class AnalysisRunner:
    def __init__(self, graph, gateway: MessagingGateway, settings: Settings):
        self._graph = graph
        self._gateway = gateway
        self._settings = settings
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def handle(self, inbound: InboundMessage, user: AllowedUser) -> None:
        """Process one inbound message end to end. Never raises."""
        analysis_id = uuid4().hex[:8]
        with structlog.contextvars.bound_contextvars(
            analysis_id=analysis_id,
            message_sid=inbound.message_sid,
            user=f"{user.name} {mask_number(user.number)}",
        ):
            queued_at = time.perf_counter()
            log.info("analysis.queued", num_media=len(inbound.media), has_body=bool(inbound.body))
            async with self._locks[user.number]:
                started_at = time.perf_counter()
                log.info("analysis.started", wait_ms=round((started_at - queued_at) * 1000))
                try:
                    await self._process(inbound, user, analysis_id, started_at)
                except Exception as e:
                    log.exception("analysis.failed", error_type=type(e).__name__)

    async def _send(self, user: AllowedUser, body: str, kind) -> None:
        await self._gateway.send(user.number, body, kind)

    def _completed(self, started_at: float, outcome: str, **fields) -> None:
        total_ms = round((time.perf_counter() - started_at) * 1000)
        log.info("analysis.completed", outcome=outcome, total_ms=total_ms, **fields)

    async def _process(
        self, inbound: InboundMessage, user: AllowedUser, analysis_id: str, started_at: float
    ) -> None:
        unsupported = [
            m for m in inbound.media
            if normalize_content_type(m.content_type) not in ALLOWED_IMAGE_TYPES
        ]
        if unsupported:
            await self._send(user, UNSUPPORTED_MEDIA_TEXT, "error")
            self._completed(
                started_at, "unsupported_media",
                mime=normalize_content_type(unsupported[0].content_type),
            )
            return
        if not inbound.media and not inbound.body:
            await self._send(user, HELP_TEXT, "help")
            self._completed(started_at, "help")
            return

        await self._gateway.acknowledge(user.number, inbound.message_sid, ACK_TEXT)

        images = []
        for media in inbound.media:
            try:
                images.append(await self._gateway.download_image(media))
            except MediaRejected as e:
                await self._send(user, _REJECT_TEXTS[e.reason], "error")
                self._completed(started_at, "media_rejected", reason=e.reason)
                return

        async def on_event(event: dict) -> None:
            if event.get("type") == "extracted":
                await self._send(user, event["progress_text"], "progress")

        config = {
            "run_name": "phishing-analysis",
            "tags": ["whatsapp"],
            "metadata": {
                "analysis_id": analysis_id,
                "message_sid": inbound.message_sid,
                "user_name": user.name,
            },
            "configurable": {"log_content": self._settings.log_content},
        }
        try:
            async with asyncio.timeout(self._settings.analysis_timeout_seconds):
                final = await run_analysis(
                    self._graph,
                    {"analysis_id": analysis_id, "caption": inbound.body or None, "images": images},
                    config=config,
                    on_event=on_event,
                )
        except TimeoutError:
            log.error(
                "analysis.failed",
                error_type="timeout",
                timeout_s=self._settings.analysis_timeout_seconds,
            )
            await self._send(user, GENERIC_ERROR_TEXT, "error")
            return
        except LLMRefusalError as e:
            log.warning("analysis.failed", error_type="refusal", category=e.category)
            await self._send(user, REFUSAL_TEXT, "error")
            return
        except Exception as e:
            log.exception("analysis.failed", error_type=type(e).__name__)
            await self._send(user, GENERIC_ERROR_TEXT, "error")
            return

        verdict = final.get("verdict")
        await self._send(user, final["reply_text"], "final" if verdict else "help")
        self._completed(
            started_at,
            "verdict" if verdict else "help",
            label=verdict.label if verdict else None,
        )
