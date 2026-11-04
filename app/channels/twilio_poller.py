"""Inbound side of Twilio without a webhook: poll the Messages API.

Useful for local development: no ngrok, no public URL, no signature to verify.
Every `poll_interval_seconds` we list the messages sent to our WhatsApp sender
since the last check and hand the new inbound ones to `on_message`.
See docs/twilio.md §13.
"""

import asyncio
import threading
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
PAGE_SIZE = 100  # per HTTP page; all pages in the window are read
MAX_SEEN = 1000
TWILIO_API = "https://api.twilio.com"


async def _in_daemon_thread(fn):
    """Run a blocking call in a daemon thread.

    Unlike asyncio.to_thread, an in-flight call never delays shutdown: the default
    executor is joined when the event loop closes, a daemon thread is not.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(setter, value):
        if not future.done():
            setter(value)

    def target():
        try:
            result = fn()
        except BaseException as e:  # noqa: BLE001 - re-raised in the awaiting task
            setter, value = future.set_exception, e
        else:
            setter, value = future.set_result, result
        try:
            loop.call_soon_threadsafe(deliver, setter, value)
        except RuntimeError:
            pass  # loop already closed: nobody is waiting any more

    threading.Thread(target=target, name="twilio-poller-fetch", daemon=True).start()
    return await future


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
        self._started_at = clock()
        self._since = self._started_at
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
        """Blocking Twilio calls (run in a thread): new inbound messages, oldest first.

        A message whose date or media cannot be read is logged, remembered and skipped,
        so it can never block the messages behind it.
        """
        messages = self._client.messages.list(
            to=self._settings.twilio_whatsapp_from,
            date_sent_after=self._since - OVERLAP,
            page_size=PAGE_SIZE,
        )
        new = []
        for m in messages:
            if m.direction != "inbound" or m.sid in self._seen:
                continue
            if m.date_sent is None:
                self._skip(m.sid, "missing_date")
                continue
            if m.date_sent < self._started_at:
                self._remember(m.sid)  # arrived before this process started
                continue
            new.append(m)
        new.sort(key=lambda m: m.date_sent)

        batch = []
        for m in new:
            try:
                batch.append((m, self._media(m)))
            except Exception as e:  # noqa: BLE001 - e.g. a media lookup that keeps failing
                self._skip(m.sid, "media_unreadable", error_type=type(e).__name__)
        return batch

    def _skip(self, sid: str, reason: str, **fields) -> None:
        self._remember(sid)
        log.warning("poller.message_skipped", message_sid=sid, reason=reason, **fields)

    async def poll_once(self) -> int:
        """One polling round; returns how many new messages were dispatched. Never raises."""
        try:
            batch = await _in_daemon_thread(self._fetch)
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
            try:
                await self._on_message(inbound)
            except Exception:
                log.exception("poller.dispatch_failed", message_sid=message.sid)
        return len(batch)

    async def run(self, stop: asyncio.Event) -> None:
        log.info(
            "poller.started",
            interval_s=self._settings.poll_interval_seconds,
            to=self._settings.twilio_whatsapp_from,
        )
        try:
            while not stop.is_set():
                await self.poll_once()
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=self._settings.poll_interval_seconds
                    )
                except TimeoutError:
                    pass
        finally:
            log.info("poller.stopped")
