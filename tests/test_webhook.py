import structlog
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import create_app
from app.whitelist import Whitelist
from tests.conftest import WHITELISTED_NUMBER
from tests.fakes import FakeGateway, FakeLLM, make_extraction, make_verdict

WEBHOOK = "/webhooks/twilio/whatsapp"
STATUS = "/webhooks/twilio/status"


def signed_post(client, path, params, token="test-token"):
    signature = RequestValidator(token).compute_signature("https://test.example" + path, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def params(sid="MM1", frm=f"whatsapp:{WHITELISTED_NUMBER}", body="Il tuo conto è sospeso"):
    return {"MessageSid": sid, "From": frm, "Body": body, "NumMedia": "0"}


def test_invalid_signature_403_logs_expected_url(app_client, gateway):
    with structlog.testing.capture_logs() as logs:
        r = app_client.post(WEBHOOK, data=params(), headers={"X-Twilio-Signature": "bad"})
    assert r.status_code == 403
    ev = next(e for e in logs if e["event"] == "webhook.signature_invalid")
    assert ev["expected_url"] == "https://test.example/webhooks/twilio/whatsapp"
    assert gateway.sent == []


def test_missing_signature_403(app_client):
    assert app_client.post(WEBHOOK, data=params()).status_code == 403


def test_not_whitelisted_gets_nothing(app_client, gateway):
    with structlog.testing.capture_logs() as logs:
        r = signed_post(app_client, WEBHOOK, params(frm="whatsapp:+391111111111"))
    assert r.status_code == 200 and "<Response></Response>" in r.text
    assert gateway.sent == []
    ev = next(e for e in logs if e["event"] == "webhook.not_whitelisted")
    assert ev["user_masked"] == "+39***1111"


def test_whitelisted_text_runs_analysis(app_client, gateway):
    r = signed_post(app_client, WEBHOOK, params())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Response></Response>" in r.text
    assert [k for _, _, k in gateway.sent] == ["ack", "progress", "final"]


def test_duplicate_message_processed_once(app_client, gateway):
    with structlog.testing.capture_logs() as logs:
        signed_post(app_client, WEBHOOK, params(sid="MM9"))
        signed_post(app_client, WEBHOOK, params(sid="MM9"))
    assert [k for _, _, k in gateway.sent].count("final") == 1
    assert any(e["event"] == "webhook.duplicate" for e in logs)


def test_status_callback_logged(app_client):
    with structlog.testing.capture_logs() as logs:
        r = signed_post(
            app_client,
            STATUS,
            {
                "MessageSid": "SM1",
                "MessageStatus": "failed",
                "ErrorCode": "63015",
                "To": f"whatsapp:{WHITELISTED_NUMBER}",
            },
        )
    assert r.status_code == 204
    ev = next(e for e in logs if e["event"] == "twilio.status.updated")
    assert ev["status"] == "failed" and ev["error_code"] == "63015"
    assert WHITELISTED_NUMBER not in str(ev)


def test_status_callback_requires_signature(app_client):
    assert app_client.post(STATUS, data={"MessageSid": "SM1"}).status_code == 403


def test_signature_check_can_be_disabled(settings):
    settings.validate_twilio_signature = False
    gateway = FakeGateway()
    client = TestClient(
        create_app(
            settings,
            llm=FakeLLM(make_extraction(), make_verdict()),
            gateway=gateway,
            whitelist=Whitelist(settings.whitelist_path),
        )
    )
    assert client.post(WEBHOOK, data=params()).status_code == 200
    assert [k for _, _, k in gateway.sent] == ["ack", "progress", "final"]


def test_health_reports_whitelist(app_client):
    assert app_client.get("/health").json()["whitelist_entries"] == 1
