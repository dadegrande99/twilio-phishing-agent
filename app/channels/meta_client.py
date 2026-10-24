"""Outbound side of the WhatsApp Cloud API (Meta): send text, read receipt + typing, media.

See docs/meta-whatsapp.md.
"""

import time
from urllib.parse import urlsplit

import httpx

from app.agent.schemas import ImageInput
from app.channels.models import MediaRef, MessageKind
from app.config import Settings
from app.logging import get_logger, mask_number, mask_numbers_in_text
from app.media import (
    ALLOWED_IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    MediaRejected,
    normalize_content_type,
    validate_image,
)

log = get_logger(__name__)

TIMEOUT_SECONDS = 15
# The access token is only ever sent over https to these Meta-owned domains.
_META_HOST_SUFFIXES = ("facebook.com", "fbsbx.com", "fbcdn.net", "whatsapp.net")


def _is_meta_url(url: str) -> bool:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix) for suffix in _META_HOST_SUFFIXES
    )


def _error_fields(response: httpx.Response) -> dict:
    try:
        error = response.json().get("error") or {}
    except ValueError:
        error = {}
    return {
        "status": response.status_code,
        "error_code": error.get("code"),
        # Error texts can contain the recipient's number.
        "error": mask_numbers_in_text(str(error.get("message", ""))),
    }


class MetaGateway:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None):
        self._settings = settings
        self._http = http or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._base = f"https://graph.facebook.com/{settings.meta_graph_version}"

    def _auth(self) -> dict[str, str]:
        # Per request (not a client default) so it can never leak to another host.
        return {"Authorization": f"Bearer {self._settings.meta_access_token.get_secret_value()}"}

    async def _post_message(self, payload: dict) -> httpx.Response:
        return await self._http.post(
            f"{self._base}/{self._settings.meta_phone_number_id}/messages",
            json={"messaging_product": "whatsapp", **payload},
            headers=self._auth(),
        )

    async def send(self, to: str, body: str, kind: MessageKind) -> str | None:
        start = time.perf_counter()
        fields = {"kind": kind, "to": mask_number(to)}
        try:
            response = await self._post_message(
                {
                    "recipient_type": "individual",
                    "to": to.lstrip("+"),
                    "type": "text",
                    "text": {"preview_url": False, "body": body},
                }
            )
        except httpx.HTTPError as e:
            log.error("meta.message.failed", **fields, error=type(e).__name__)
            return None
        ms = round((time.perf_counter() - start) * 1000)
        if response.status_code != 200:
            log.error("meta.message.failed", **fields, **_error_fields(response), ms=ms)
            return None
        message_id = (response.json().get("messages") or [{}])[0].get("id")
        log.info("meta.message.sent", **fields, message_id=message_id, chars=len(body), ms=ms)
        return message_id

    async def acknowledge(self, to: str, message_id: str, ack_text: str) -> None:
        # Native ack: blue ticks + "typing…" (cleared by our next message or after 25 s).
        # ack_text is not needed on this channel.
        try:
            response = await self._post_message(
                {
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                }
            )
        except httpx.HTTPError as e:
            log.warning("meta.ack.failed", error=type(e).__name__)
            return
        if response.status_code != 200:
            log.warning("meta.ack.failed", **_error_fields(response))
            return
        log.info("meta.ack.sent", message_id=message_id)

    async def download_image(self, media: MediaRef) -> ImageInput:
        start = time.perf_counter()
        if normalize_content_type(media.content_type) not in ALLOWED_IMAGE_TYPES:
            log.warning("media.rejected", reason="unsupported_type")
            raise MediaRejected("unsupported_type")

        # Step 1: media id → temporary download URL (valid ~5 minutes).
        try:
            lookup = await self._http.get(
                f"{self._base}/{media.locator}",
                params={"phone_number_id": self._settings.meta_phone_number_id},
                headers=self._auth(),
            )
        except httpx.HTTPError as e:
            log.warning("media.rejected", reason="download_failed", error=type(e).__name__)
            raise MediaRejected("download_failed") from e
        if lookup.status_code != 200:
            log.warning("media.rejected", reason="download_failed", **_error_fields(lookup))
            raise MediaRejected("download_failed")
        info = lookup.json()
        if int(info.get("file_size") or 0) > MAX_IMAGE_BYTES:
            log.warning("media.rejected", reason="too_large", bytes=info.get("file_size"))
            raise MediaRejected("too_large")
        url = info.get("url", "")
        if not _is_meta_url(url):
            log.warning("media.rejected", reason="download_failed", error="not_a_meta_url")
            raise MediaRejected("download_failed")

        # Step 2: download the binary (token required).
        try:
            response = await self._http.get(url, headers=self._auth())
        except httpx.HTTPError as e:
            log.warning("media.rejected", reason="download_failed", error=type(e).__name__)
            raise MediaRejected("download_failed") from e
        if response.status_code != 200:
            log.warning("media.rejected", reason="download_failed", status=response.status_code)
            raise MediaRejected("download_failed")

        content_type = response.headers.get("content-type") or info.get("mime_type", "")
        try:
            image = validate_image(content_type, response.content)
        except MediaRejected as e:
            log.warning("media.rejected", reason=e.reason, bytes=len(response.content))
            raise
        log.info(
            "media.downloaded",
            mime=image.mime,
            bytes=len(response.content),
            ms=round((time.perf_counter() - start) * 1000),
        )
        return image
