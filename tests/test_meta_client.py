import json

import httpx
import pytest
import structlog

from app.channels.meta_client import MetaGateway
from app.channels.models import MediaRef
from app.logging import mask_numbers_in_text
from app.media import MAX_IMAGE_BYTES, MediaRejected

GRAPH = "https://graph.facebook.com/v26.0"


def gateway(meta_settings, handler):
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return MetaGateway(meta_settings, http=http), seen


def test_mask_numbers_in_text():
    assert mask_numbers_in_text("to +393331234567 now") == "to +39***4567 now"


async def test_send_text(meta_settings):
    gw, seen = gateway(
        meta_settings, lambda r: httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})
    )
    assert await gw.send("+393331234567", "ciao", "final") == "wamid.OUT"
    request = seen[0]
    assert request.method == "POST" and str(request.url) == f"{GRAPH}/PNID/messages"
    assert request.headers["authorization"] == "Bearer meta-token"
    body = json.loads(request.content)
    assert body["messaging_product"] == "whatsapp" and body["type"] == "text"
    assert body["to"] == "393331234567" and body["text"]["body"] == "ciao"


async def test_send_logs_masked(meta_settings):
    gw, _ = gateway(meta_settings, lambda r: httpx.Response(200, json={"messages": [{"id": "w"}]}))
    with structlog.testing.capture_logs() as logs:
        await gw.send("+393331234567", "x", "progress")
    ev = next(e for e in logs if e["event"] == "meta.message.sent")
    assert ev["to"] == "+39***4567" and ev["kind"] == "progress"
    assert "+393331234567" not in str(logs) and "393331234567" not in str(logs)


async def test_send_error_returns_none_and_masks(meta_settings):
    error = {"error": {"code": 131030, "message": "Recipient +393331234567 not in allowed list"}}
    gw, _ = gateway(meta_settings, lambda r: httpx.Response(400, json=error))
    with structlog.testing.capture_logs() as logs:
        assert await gw.send("+393331234567", "x", "final") is None
    ev = next(e for e in logs if e["event"] == "meta.message.failed")
    assert ev["error_code"] == 131030 and ev["status"] == 400
    assert "+393331234567" not in str(logs)


async def test_send_network_error_returns_none(meta_settings):
    def boom(request):
        raise httpx.ConnectError("down")

    gw, _ = gateway(meta_settings, boom)
    assert await gw.send("+393331234567", "x", "ack") is None


async def test_acknowledge_marks_read_with_typing(meta_settings):
    gw, seen = gateway(meta_settings, lambda r: httpx.Response(200, json={"success": True}))
    await gw.acknowledge("+393331234567", "wamid.1", "ignored text")
    assert json.loads(seen[0].content) == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.1",
        "typing_indicator": {"type": "text"},
    }


async def test_acknowledge_failure_does_not_raise(meta_settings):
    gw, _ = gateway(meta_settings, lambda r: httpx.Response(500, json={"error": {"code": 1}}))
    with structlog.testing.capture_logs() as logs:
        await gw.acknowledge("+393331234567", "wamid.1", "x")
    assert any(e["event"] == "meta.ack.failed" for e in logs)


def media_handler(lookup: dict, binary=b"\xff\xd8img", status=200):
    def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(status, json=lookup)
        return httpx.Response(200, content=binary, headers={"content-type": "image/jpeg"})

    return handler


async def test_download_two_steps(meta_settings):
    lookup = {"url": "https://lookaside.fbsbx.com/x", "mime_type": "image/jpeg", "file_size": 10}
    gw, seen = gateway(meta_settings, media_handler(lookup))
    image = await gw.download_image(MediaRef("MEDIA1", "image/jpeg"))
    assert image.mime == "image/jpeg"
    assert str(seen[0].url) == f"{GRAPH}/MEDIA1?phone_number_id=PNID"
    assert seen[1].url.host == "lookaside.fbsbx.com"
    assert all(r.headers["authorization"] == "Bearer meta-token" for r in seen)


async def test_download_too_large_skips_binary(meta_settings):
    lookup = {"url": "https://lookaside.fbsbx.com/x", "mime_type": "image/jpeg",
              "file_size": MAX_IMAGE_BYTES + 1}
    gw, seen = gateway(meta_settings, media_handler(lookup))
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MediaRef("MEDIA1", "image/jpeg"))
    assert e.value.reason == "too_large" and len(seen) == 1


@pytest.mark.parametrize("url", ["https://evil.example/x", "http://lookaside.fbsbx.com/x",
                                 "https://fbsbx.com.evil.example/x"])
async def test_download_refuses_foreign_host(meta_settings, url):
    lookup = {"url": url, "mime_type": "image/jpeg", "file_size": 10}
    gw, seen = gateway(meta_settings, media_handler(lookup))
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MediaRef("MEDIA1", "image/jpeg"))
    assert e.value.reason == "download_failed" and len(seen) == 1


async def test_download_rejects_audio_without_network(meta_settings):
    gw, seen = gateway(meta_settings, media_handler({}))
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MediaRef("A1", "audio/ogg"))
    assert e.value.reason == "unsupported_type" and seen == []


async def test_download_media_lookup_error(meta_settings):
    gw, _ = gateway(meta_settings, media_handler({"error": {"code": 100}}, status=404))
    with pytest.raises(MediaRejected) as e:
        await gw.download_image(MediaRef("MEDIA1", "image/jpeg"))
    assert e.value.reason == "download_failed"
