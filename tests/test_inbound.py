from types import SimpleNamespace

import structlog

from app.agent.runner import MessageDeduper
from app.api.inbound import accept_inbound
from app.channels.models import InboundMessage
from app.whitelist import AllowedUser, Whitelist
from tests.conftest import WHITELISTED_NUMBER


def state(settings):
    return SimpleNamespace(whitelist=Whitelist(settings.whitelist_path), deduper=MessageDeduper())


def message(sid="SM1", number=WHITELISTED_NUMBER):
    return InboundMessage(sid, number, "ciao", [], None)


def test_accept_whitelisted(settings):
    with structlog.testing.capture_logs() as logs:
        user = accept_inbound(state(settings), message(), source="twilio_polling")
    assert user == AllowedUser(WHITELISTED_NUMBER, "Davide")
    ev = next(e for e in logs if e["event"] == "inbound.received")
    assert ev["source"] == "twilio_polling" and ev["message_sid"] == "SM1"


def test_reject_not_whitelisted(settings):
    with structlog.testing.capture_logs() as logs:
        assert accept_inbound(state(settings), message(number="+391111111111"),
                              source="twilio_webhook") is None
    ev = next(e for e in logs if e["event"] == "inbound.not_whitelisted")
    assert ev["user_masked"] == "+39***1111"


def test_reject_duplicate(settings):
    st = state(settings)
    assert accept_inbound(st, message(), source="meta_webhook") is not None
    with structlog.testing.capture_logs() as logs:
        assert accept_inbound(st, message(), source="meta_webhook") is None
    assert any(e["event"] == "inbound.duplicate" for e in logs)
