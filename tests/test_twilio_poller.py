import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import structlog
from fastapi.testclient import TestClient

from app.channels.models import InboundMessage, MediaRef
from app.channels.twilio_poller import TwilioPoller
from app.main import create_app
from app.whitelist import Whitelist
from tests.fakes import FakeGateway, FakeLLM, make_extraction, make_verdict

START = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def msg(sid, seconds, direction="inbound", num_media=0, body=" ciao "):
    return SimpleNamespace(
        sid=sid, direction=direction, from_="whatsapp:+393331234567", body=body,
        num_media=str(num_media), date_sent=START + timedelta(seconds=seconds),
    )


class FakeTwilio:
    def __init__(self, pages, media=None, error=None):
        self.pages = list(pages)
        self.media_by_sid = media or {}
        self.error = error
        self.list_calls = []
        self.messages = self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.error:
            error, self.error = self.error, None
            raise error
        return self.pages.pop(0) if self.pages else []

    def __call__(self, sid):
        media = self.media_by_sid.get(sid, [])
        return SimpleNamespace(media=SimpleNamespace(list=lambda **kw: media))


def poller(settings, client):
    received: list[InboundMessage] = []

    async def on_message(message):
        received.append(message)

    return TwilioPoller(settings, client=client, on_message=on_message, clock=lambda: START), received


async def test_dispatches_new_inbound_in_order(settings):
    client = FakeTwilio([[msg("SM2", 20), msg("OUT", 15, direction="outbound-api"), msg("SM1", 10)]])
    p, received = poller(settings, client)
    assert await p.poll_once() == 2
    assert [m.message_sid for m in received] == ["SM1", "SM2"]
    assert received[0] == InboundMessage("SM1", "+393331234567", "ciao", [], None)


async def test_same_sid_not_dispatched_twice(settings):
    client = FakeTwilio([[msg("SM1", 10)], [msg("SM1", 10)]])
    p, received = poller(settings, client)
    await p.poll_once()
    assert await p.poll_once() == 0
    assert len(received) == 1


async def test_filters_by_sender_and_since(settings):
    client = FakeTwilio([[msg("SM1", 30)], []])
    p, _ = poller(settings, client)
    await p.poll_once()
    await p.poll_once()
    first, second = client.list_calls
    assert first["to"] == settings.twilio_whatsapp_from
    assert first["date_sent_after"] == START - timedelta(seconds=60)
    # since advances to the newest message seen (minus the overlap window)
    assert second["date_sent_after"] == START + timedelta(seconds=30) - timedelta(seconds=60)


async def test_media_refs_built(settings):
    media = {"MM1": [SimpleNamespace(
        content_type="image/jpeg",
        uri="/2010-04-01/Accounts/AC/Messages/MM1/Media/ME1.json",
    )]}
    client = FakeTwilio([[msg("MM1", 5, num_media=1, body="")]], media=media)
    p, received = poller(settings, client)
    await p.poll_once()
    assert received[0].media == [
        MediaRef("https://api.twilio.com/2010-04-01/Accounts/AC/Messages/MM1/Media/ME1", "image/jpeg")
    ]


async def test_network_error_logged_and_loop_survives(settings):
    client = FakeTwilio([[msg("SM1", 5)]], error=ConnectionError("Failed to resolve +393331234567"))
    p, received = poller(settings, client)
    with structlog.testing.capture_logs() as logs:
        assert await p.poll_once() == 0
    ev = next(e for e in logs if e["event"] == "poller.error")
    assert ev["error_type"] == "ConnectionError" and "+393331234567" not in str(ev)
    assert await p.poll_once() == 1 and len(received) == 1


async def test_run_stops_on_event(settings):
    settings.poll_interval_seconds = 0.01
    p, _ = poller(settings, FakeTwilio([]))
    stop = asyncio.Event()
    with structlog.testing.capture_logs() as logs:
        task = asyncio.create_task(p.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, 1)
    events = [e["event"] for e in logs]
    assert events[0] == "poller.started" and events[-1] == "poller.stopped"


def test_polling_app_has_no_twilio_webhook(settings):
    settings.inbound_mode = "polling"
    app = create_app(
        settings,
        llm=FakeLLM(make_extraction(), make_verdict()),
        gateway=FakeGateway(),
        whitelist=Whitelist(settings.whitelist_path),
    )
    client = TestClient(app)
    assert client.post("/webhooks/twilio/whatsapp", data={}).status_code == 404
    assert client.get("/health").json()["inbound_mode"] == "polling"
