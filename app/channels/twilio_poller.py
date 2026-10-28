"""Inbound side of Twilio without a webhook: poll the Messages API.

Useful for local development: no ngrok, no public URL, no signature to verify.
Every `poll_interval_seconds` we list the messages sent to our WhatsApp sender
since the last check and hand the new inbound ones to `on_message`.
See docs/twilio.md §13.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from app.channels.models import InboundMessage, MediaRef
from app.config import Settings
from app.logging import get_logger, mask_numbers_in_text

log = get_logger(__name__)

# Re-read a small window before the newest message seen, in case Twilio stores
# messages slightly out of order; already-seen SIDs are skipped.
OVERLAP = timedelta(seconds=60)
PAGE_SIZE = 100
MAX_SEEN = 1000
TWILIO_API = "https://api.twilio.com"


class TwilioPoller:
    def __init__(
        self,
        settings: Settings,
        *,
        client,
        on_message: Callable[[InboundMessage], Awaitable[None]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self._settings = settings
        self._client = client
        self._on_message = on_message
        # Start from "now": messages received before startup are not analysed.
        self._since = clock()
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _remember(self, sid: str) -> None:
        self._seen[sid] = None
        while len(self._seen) > MAX_SEEN:
            self._seen.popitem(last=False)

    def _media(self, message) -> list[MediaRef]:
        if int(message.num_media or 0) == 0:
            return []
        items = self._client.messages(message.sid).media.list(limit=10)
        return [
            MediaRef(locator=TWILIO_API + item.uri.removesuffix(".json"),
                     content_type=item.content_type)
            for item in items
        ]

    def _fetch(self) -> list[tuple[object, list[MediaRef]]]:
        """Blocking Twilio calls (run in a thread): new inbound messages, oldest first."""
        messages = self._client.messages.list(
            to=self._settings.twilio_whatsapp_from,
            date_sent_after=self._since - OVERLAP,
            limit=PAGE_SIZE,
        )
        new = [m for m in messages if m.direction == "inbound" and m.sid not in self._seen]
        new.sort(key=lambda m: m.date_sent)
        return [(m, self._media(m)) for m in new]

    async def poll_once(self) -> int:
        """One polling round; returns how many new messages were dispatched. Never raises."""
        try:
            batch = await asyncio.to_thread(self._fetch)
        except Exception as e:  # noqa: BLE001 - network/DNS errors must not stop polling
            log.warning(
                "poller.error",
                error_type=type(e).__name__,
                error=mask_numbers_in_text(str(e))[:300],
            )
            return 0
        for message, media in batch:
            self._remember(message.sid)
            self._since = max(self._since, message.date_sent)
            inbound = InboundMessage(
                message_sid=message.sid,
                from_number=(message.from_ or "").removeprefix("whatsapp:"),
                body=(message.body or "").strip(),
                media=media,
                profile_name=None,
            )
            log.info("poller.message_found", message_sid=message.sid, num_media=len(media))
            await self._on_message(inbound)
        return len(batch)

    async def run(self, stop: asyncio.Event) -> None:
        log.info(
            "poller.started",
            interval_s=self._settings.poll_interval_seconds,
            to=self._settings.twilio_whatsapp_from,
        )
        while not stop.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.poll_interval_seconds)
            except TimeoutError:
                pass
        log.info("poller.stopped")
