import structlog
from fastapi.testclient import TestClient

from app.main import create_app
from tests.fakes import FakeLLM, make_extraction, make_verdict


def client_for(settings, llm=None) -> TestClient:
    return TestClient(create_app(settings, llm=llm or FakeLLM(make_extraction(), make_verdict())))


def test_health(settings):
    r = client_for(settings).get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["model"] == "claude-haiku-5-5"


def test_debug_disabled_by_default(settings):
    assert client_for(settings).post("/debug/analyze", data={"caption": "x"}).status_code == 404


def test_debug_analyze_image(settings):
    settings.debug = True
    llm = FakeLLM(make_extraction(), make_verdict(label="phishing"))
    r = client_for(settings, llm).post(
        "/debug/analyze",
        files={"image": ("s.png", b"\x89PNG", "image/png")},
        data={"caption": "?"},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["verdict"]["label"] == "phishing"
    assert body["events"][0]["type"] == "extracted"
    assert body["reply_text"].startswith("🔴 PHISHING")
    assert body["analysis_id"]


def test_debug_analyze_text_only(settings):
    settings.debug = True
    r = client_for(settings).post("/debug/analyze", data={"caption": "Il tuo conto è bloccato"})
    assert r.status_code == 200 and r.json()["extraction"]["channel"] == "sms"


def test_debug_rejects_audio(settings):
    settings.debug = True
    r = client_for(settings).post("/debug/analyze", files={"image": ("a.ogg", b"x", "audio/ogg")})
    assert r.status_code == 415


def test_debug_requires_image_or_caption(settings):
    settings.debug = True
    assert client_for(settings).post("/debug/analyze").status_code == 422


def test_request_is_logged(settings):
    client = client_for(settings)
    with structlog.testing.capture_logs() as logs:
        client.get("/health")
    assert any(e["event"] == "http.request" and e["path"] == "/health" for e in logs)


def test_debug_not_reachable_through_tunnel(settings):
    # ngrok adds X-Forwarded-For: debug must only answer local requests.
    settings.debug = True
    r = client_for(settings).post(
        "/debug/analyze", data={"caption": "x"}, headers={"X-Forwarded-For": "203.0.113.5"}
    )
    assert r.status_code == 404
