"""Outbound side of Twilio: sending WhatsApp messages and downloading media.

See docs/twilio.md (§6 media, §7 sending and status callbacks).
"""

import asyncio
import re
import time
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from app.agent.schemas import ImageInput
from app.channels.models import MediaRef
from app.channels.twilio_security import public_url
from app.config import Settings
from app.logging import get_logger, mask_number
from app.media import MediaRejected, normalize_content_type, validate_image

log = get_logger(__name__)

MessageKind = Literal["ack", "progress", "final", "help", "error"]

DOWNLOAD_TIMEOUT_SECONDS = 15
SEND_TIMEOUT_SECONDS = 15
# Twilio media URLs live on api.twilio.com; never send our credentials anywhere else.
TWILIO_MEDIA_HOST = "api.twilio.com"

_PHONE = re.compile(r"\+\d{8,15}")


def _mask_numbers(text: str) -> str:
    return _PHONE.sub(lambda m: mask_number(m.group(0)), text)


class MessagingGateway(Protocol):
    async def send(self, to: str, body: str, kind: MessageKind) -> str | None:
        """Send a WhatsApp message; returns the Twilio SID, or None on failure. Never raises."""
        ...

    async def download_image(self, media: MediaRef) -> ImageInput:
        """Download and validate an inbound image; raises MediaRejected."""
        ...


class TwilioGateway:
    def __init__(
        self,
        settings: Settings,
        *,
        client: Client | None = None,
        http: httpx.AsyncClient | None = None,
    ):
        self._settings = settings
        self._client = client
        self._http = http or httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS)

    def _twilio(self) -> Client:
        # Created lazily so the app can start before Twilio credentials are configured.
        if self._client is None:
            self._client = Client(
                self._settings.twilio_account_sid,
                self._settings.twilio_auth_token.get_secret_value(),
                # Default is no timeout: a hung request would hold the user's lock forever.
                http_client=TwilioHttpClient(timeout=SEND_TIMEOUT_SECONDS),
            )
        return self._client

    async def send(self, to: str, body: str, kind: MessageKind) -> str | None:
        start = time.perf_counter()
        try:
            message = await asyncio.to_thread(
                self._twilio().messages.create,
                from_=self._settings.twilio_whatsapp_from,
                to=f"whatsapp:{to}",
                body=body,
                status_callback=public_url(self._settings, "/webhooks/twilio/status"),
            )
        except Exception as e:  # noqa: BLE001 - a failed send must never break the analysis
            log.error(
                "twilio.message.failed",
                kind=kind,
                to=mask_number(to),
                error_code=getattr(e, "code", None),
                # Twilio error texts can contain the recipient's number.
                error=_mask_numbers(str(getattr(e, "msg", "") or e)),
                ms=round((time.perf_counter() - start) * 1000),
            )
            return None
        log.info(
            "twilio.message.sent",
            kind=kind,
            to=mask_number(to),
            twilio_sid=message.sid,
            chars=len(body),
            ms=round((time.perf_counter() - start) * 1000),
        )
        return message.sid

    async def download_image(self, media: MediaRef) -> ImageInput:
        start = time.perf_counter()
        url = urlsplit(media.locator)
        if url.scheme != "https" or url.hostname != TWILIO_MEDIA_HOST:
            log.warning("media.rejected", reason="download_failed", error="not_a_twilio_url")
            raise MediaRejected("download_failed")
        try:
            response = await self._http.get(
                media.locator,
                auth=(
                    self._settings.twilio_account_sid,
                    self._settings.twilio_auth_token.get_secret_value(),
                ),
                follow_redirects=True,
            )
        except httpx.HTTPError as e:
            log.warning("media.rejected", reason="download_failed", error=type(e).__name__)
            raise MediaRejected("download_failed") from e
        if response.status_code != 200:
            log.warning("media.rejected", reason="download_failed", status=response.status_code)
            raise MediaRejected("download_failed")

        content_type = response.headers.get("content-type", media.content_type)
        try:
            image = validate_image(content_type, response.content)
        except MediaRejected as e:
            log.warning(
                "media.rejected",
                reason=e.reason,
                mime=normalize_content_type(content_type),
                bytes=len(response.content),
            )
            raise
        log.info(
            "media.downloaded",
            mime=image.mime,
            bytes=len(response.content),
            ms=round((time.perf_counter() - start) * 1000),
        )
        return image
