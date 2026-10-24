from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
import structlog
from twilio.base.exceptions import TwilioRestException

from app.channels.twilio_client import TwilioGateway
from app.channels.twilio_security import MediaRef
from app.media import MediaRejected

MEDIA = MediaRef("https://api.twilio.com/m/ME1", "image/jpeg")


def twilio_mock(sid="SM123"):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(sid=sid)
    return client


def gateway_with_http(settings, handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TwilioGateway(settings, client=MagicMock(), http=http)


async def test_send_formats_whatsapp_and_status_callback(settings):
    tw = twilio_mock()
    sid = await TwilioGateway(settings, client=tw).send("+393331234567", "ciao", "ack")
    assert sid == "SM123"
    kw = tw.messages.create.call_args.kwargs
    assert kw["to"] == "whatsapp:+393331234567"
    assert kw["from_"] == "whatsapp:+14155238886"
    assert kw["body"] == "ciao"
    assert kw["status_callback"] == "https://test.example/webhooks/twilio/status"


async def test_send_logs_masked_number(settings):
    with structlog.testing.capture_logs() as logs:
        await TwilioGateway(settings, client=twilio_mock()).send("+393331234567", "x", "final")
    ev = next(e for e in logs if e["event"] == "twilio.message.sent")
    assert ev["to"] == "+39***4567" and ev["kind"] == "final" and ev["twilio_sid"] == "SM123"
    assert "+393331234567" not in str(logs)


async def test_send_failure_returns_none_and_logs(settings):
    tw = MagicMock()
    tw.messages.create.side_effect = TwilioRestException(400, "u", msg="x", code=63015)
    with structlog.testing.capture_logs() as logs:
        assert await TwilioGateway(settings, client=tw).send("+39333", "x", "progress") is None
    ev = next(e for e in logs if e["event"] == "twilio.message.failed")
    assert ev["error_code"] == 63015 and ev["kind"] == "progress"


async def test_send_unexpected_error_returns_none(settings):
    tw = MagicMock()
    tw.messages.create.side_effect = ConnectionError("down")
    assert await TwilioGateway(settings, client=tw).send("+39333", "x", "ack") is None


async def test_download_uses_basic_auth(settings):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, content=b"\xff\xd8", headers={"content-type": "image/jpeg"})

    img = await gateway_with_http(settings, handler).download_image(MEDIA)
    assert img.mime == "image/jpeg"
    assert seen["auth"] == httpx.BasicAuth("ACtest", "test-token")._auth_header


async def test_download_follows_redirect(settings):
    def handler(request):
        if request.url.host == "api.twilio.com":
            return httpx.Response(307, headers={"location": "https://media.example/x"})
        return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})

    assert (await gateway_with_http(settings, handler).download_image(MEDIA)).mime == "image/png"


async def test_download_http_error_rejected(settings):
    gw = gateway_with_http(settings, lambda request: httpx.Response(404))
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MEDIA)
    assert e.value.reason == "download_failed"


async def test_download_network_error_rejected(settings):
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(MediaRejected) as e:
        await gateway_with_http(settings, handler).download_image(MEDIA)
    assert e.value.reason == "download_failed"


async def test_download_wrong_type_rejected(settings):
    gw = gateway_with_http(
        settings,
        lambda request: httpx.Response(200, content=b"x", headers={"content-type": "audio/ogg"}),
    )
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MEDIA)
    assert e.value.reason == "unsupported_type"


def test_lazy_twilio_client_has_timeout(settings):
    client = TwilioGateway(settings)._twilio()
    assert client.http_client.timeout == 15


async def test_send_failure_masks_numbers_in_error(settings):
    tw = MagicMock()
    tw.messages.create.side_effect = TwilioRestException(
        400, "u", msg="The 'To' number +393331234567 is not a valid phone number.", code=21211
    )
    with structlog.testing.capture_logs() as logs:
        await TwilioGateway(settings, client=tw).send("+393331234567", "x", "ack")
    assert "+393331234567" not in str(logs)
    assert "+39***4567" in str(logs)


@pytest.mark.parametrize(
    "url", ["https://attacker.example/m/ME1", "http://api.twilio.com/m/ME1"]
)
async def test_download_refuses_non_twilio_urls(settings, url):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x", headers={"content-type": "image/png"})

    with pytest.raises(MediaRejected) as e:
        await gateway_with_http(settings, handler).download_image(MediaRef(url, "image/png"))
    assert e.value.reason == "download_failed"
    assert calls == []  # credentials never sent


async def test_twilio_acknowledge_sends_ack_text(settings):
    tw = twilio_mock()
    await TwilioGateway(settings, client=tw).acknowledge("+393331234567", "MM1", "ok!")
    assert tw.messages.create.call_args.kwargs["body"] == "ok!"
