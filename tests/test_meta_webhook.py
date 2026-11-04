import hashlib
import hmac
import json

import structlog

from tests.fakes import meta_payload, text_msg

NUMBER_RAW = "393331234567"
VERIFY = {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"}


def signed(client, payload, secret="app-secret"):
    body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def kinds(gateway):
    return [kind for _, _, kind in gateway.sent]


def test_verify_handshake(meta_client):
    r = meta_client.get("/webhooks/meta", params=VERIFY)
    assert r.status_code == 200 and r.text == "12345"
    assert r.headers["content-type"].startswith("text/plain")


def test_verify_wrong_token_403(meta_client):
    with structlog.testing.capture_logs() as logs:
        r = meta_client.get("/webhooks/meta", params={**VERIFY, "hub.verify_token": "nope"})
    assert r.status_code == 403
    assert any(e["event"] == "meta.verify_failed" for e in logs)


def test_bad_signature_403(meta_client, meta_gateway):
    payload = meta_payload(messages=[text_msg("wamid.1", NUMBER_RAW, "x")])
    with structlog.testing.capture_logs() as logs:
        r = signed(meta_client, payload, secret="wrong")
    assert r.status_code == 403 and meta_gateway.sent == []
    assert any(e["event"] == "webhook.signature_invalid" and e["channel"] == "meta" for e in logs)


def test_missing_signature_403(meta_client):
    assert meta_client.post("/webhooks/meta", content=b"{}").status_code == 403


def test_invalid_json_with_valid_signature_400(meta_client):
    assert signed(meta_client, b"not json").status_code == 400


def test_whitelisted_text_runs_analysis(meta_client, meta_gateway):
    r = signed(meta_client, meta_payload(messages=[text_msg("wamid.1", NUMBER_RAW, "Conto sospeso")]))
    assert r.status_code == 200
    assert kinds(meta_gateway) == ["ack", "progress", "final"]
    assert meta_gateway.acked == ["wamid.1"]
    assert all(to == "+393331234567" for to, _, _ in meta_gateway.sent)


def test_not_whitelisted_gets_nothing(meta_client, meta_gateway):
    with structlog.testing.capture_logs() as logs:
        r = signed(meta_client, meta_payload(messages=[text_msg("w", "391111111111", "x")]))
    assert r.status_code == 200
    assert meta_gateway.sent == [] and meta_gateway.acked == []
    assert any(e["event"] == "inbound.not_whitelisted" for e in logs)


def test_duplicate_wamid_processed_once(meta_client, meta_gateway):
    payload = meta_payload(messages=[text_msg("wamid.9", NUMBER_RAW, "x")])
    signed(meta_client, payload)
    signed(meta_client, payload)
    assert kinds(meta_gateway).count("final") == 1


def test_two_messages_in_one_payload(meta_client, meta_gateway):
    payload = meta_payload(
        messages=[text_msg("w1", NUMBER_RAW, "a"), text_msg("w2", NUMBER_RAW, "b")]
    )
    signed(meta_client, payload)
    assert kinds(meta_gateway).count("final") == 2


def test_status_only_payload_logged(meta_client):
    payload = meta_payload(
        statuses=[{"id": "wamid.out", "status": "failed", "recipient_id": NUMBER_RAW,
                   "errors": [{"code": 131047, "title": "Re-engagement message"}]}]
    )
    with structlog.testing.capture_logs() as logs:
        r = signed(meta_client, payload)
    assert r.status_code == 200
    ev = next(e for e in logs if e["event"] == "meta.status.updated")
    assert ev["status"] == "failed" and ev["error_code"] == 131047
    assert NUMBER_RAW not in str(logs)


def test_reaction_payload_ignored(meta_client, meta_gateway):
    payload = meta_payload(messages=[{"from": NUMBER_RAW, "id": "w", "type": "reaction",
                                      "reaction": {"message_id": "x", "emoji": "👍"}}])
    assert signed(meta_client, payload).status_code == 200
    assert meta_gateway.sent == []


def test_health_reports_channel(meta_client):
    assert meta_client.get("/health").json()["channel"] == "meta"


def test_twilio_webhook_not_mounted_on_meta(meta_client):
    assert meta_client.post("/webhooks/twilio/whatsapp", data={}).status_code == 404


def test_verify_non_ascii_token_403(meta_client):
    r = meta_client.get("/webhooks/meta", params={**VERIFY, "hub.verify_token": "é"})
    assert r.status_code == 403
