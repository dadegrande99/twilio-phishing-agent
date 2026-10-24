import asyncio

import structlog

from app.agent.formatting import (
    ACK_TEXT,
    GENERIC_ERROR_TEXT,
    HELP_TEXT,
    REFUSAL_TEXT,
    TOO_LARGE_TEXT,
    UNSUPPORTED_MEDIA_TEXT,
)
from app.agent.graph import build_graph
from app.agent.llm import LLMRefusalError
from app.agent.runner import AnalysisRunner, MessageDeduper
from app.agent.schemas import ImageInput
from app.channels.twilio_security import InboundMessage, MediaRef
from app.media import MediaRejected
from app.whitelist import AllowedUser
from tests.fakes import FakeGateway, FakeLLM, make_extraction, make_verdict

NUMBER = "+393331234567"
USER = AllowedUser(NUMBER, "Davide")
IMAGE = ImageInput("image/jpeg", "QQ==")


DEFAULT_MEDIA = (MediaRef("u1", "image/jpeg"),)


def inbound(sid="MM1", body="", media=DEFAULT_MEDIA):
    return InboundMessage(sid, NUMBER, body, list(media), None)


def kinds(gateway):
    return [kind for _, _, kind in gateway.sent]


def runner(settings, llm=None, gateway=None):
    llm = llm or FakeLLM(make_extraction(), make_verdict())
    gateway = gateway or FakeGateway(images={"u1": IMAGE})
    return AnalysisRunner(build_graph(llm), gateway, settings), llm, gateway


async def test_happy_path_sends_ack_progress_final(settings):
    llm = FakeLLM(make_extraction(channel="sms"), make_verdict(label="phishing"))
    r, _, gw = runner(settings, llm)
    await r.handle(inbound(), USER)
    assert kinds(gw) == ["ack", "progress", "final"]
    assert gw.sent[0][1] == ACK_TEXT
    assert gw.sent[2][1].startswith("🔴 PHISHING")
    assert all(to == NUMBER for to, _, _ in gw.sent)
    assert llm.extract_args == [(None, [IMAGE])]


async def test_caption_is_passed(settings):
    r, llm, _ = runner(settings)
    await r.handle(inbound(body="è vero?"), USER)
    assert llm.extract_args[0][0] == "è vero?"


async def test_audio_is_rejected_without_llm(settings):
    r, llm, gw = runner(settings, gateway=FakeGateway())
    await r.handle(inbound(media=(MediaRef("u", "audio/ogg"),)), USER)
    assert gw.sent == [(NUMBER, UNSUPPORTED_MEDIA_TEXT, "error")]
    assert llm.calls == []


async def test_text_only_is_analyzed(settings):
    r, llm, gw = runner(settings)
    await r.handle(inbound(body="Il tuo conto è sospeso", media=()), USER)
    assert kinds(gw) == ["ack", "progress", "final"]
    assert llm.extract_args == [("Il tuo conto è sospeso", [])]


async def test_conversation_gets_help_reply(settings):
    r, _, gw = runner(settings, FakeLLM(make_extraction(content_kind="conversazione"), make_verdict()))
    await r.handle(inbound(body="ciao", media=()), USER)
    assert gw.sent[-1] == (NUMBER, HELP_TEXT, "help")


async def test_empty_message_gets_help(settings):
    r, llm, gw = runner(settings)
    await r.handle(inbound(body="", media=()), USER)
    assert gw.sent == [(NUMBER, HELP_TEXT, "help")]
    assert llm.calls == []


async def test_too_large_image(settings):
    r, llm, gw = runner(settings, gateway=FakeGateway(images={"u1": MediaRejected("too_large")}))
    await r.handle(inbound(), USER)
    assert gw.sent == [(NUMBER, ACK_TEXT, "ack"), (NUMBER, TOO_LARGE_TEXT, "error")]
    assert llm.calls == []


async def test_download_failure(settings):
    gw = FakeGateway(images={"u1": MediaRejected("download_failed")})
    r, _, _ = runner(settings, gateway=gw)
    await r.handle(inbound(), USER)
    assert gw.sent[-1] == (NUMBER, GENERIC_ERROR_TEXT, "error")


async def test_refusal_message(settings):
    llm = FakeLLM(make_extraction(), make_verdict(), extract_error=LLMRefusalError("cyber"))
    r, _, gw = runner(settings, llm)
    with structlog.testing.capture_logs() as logs:
        await r.handle(inbound(), USER)
    assert gw.sent[-1] == (NUMBER, REFUSAL_TEXT, "error")
    ev = next(e for e in logs if e["event"] == "analysis.failed")
    assert ev["error_type"] == "refusal" and ev["category"] == "cyber"


async def test_unexpected_error_does_not_raise(settings):
    llm = FakeLLM(make_extraction(), make_verdict(), extract_error=RuntimeError("boom"))
    r, _, gw = runner(settings, llm)
    await r.handle(inbound(), USER)
    assert gw.sent[-1] == (NUMBER, GENERIC_ERROR_TEXT, "error")


async def test_timeout(settings):
    settings.analysis_timeout_seconds = 0.05
    r, _, gw = runner(settings, FakeLLM(make_extraction(), make_verdict(), delay=1))
    with structlog.testing.capture_logs() as logs:
        await r.handle(inbound(), USER)
    assert gw.sent[-1] == (NUMBER, GENERIC_ERROR_TEXT, "error")
    assert any(e["event"] == "analysis.failed" and e["error_type"] == "timeout" for e in logs)


async def test_send_failure_mid_analysis_still_sends_final(settings):
    r, _, gw = runner(settings, gateway=FakeGateway(images={"u1": IMAGE}, fail_kinds={"progress"}))
    await r.handle(inbound(), USER)
    assert kinds(gw) == ["ack", "final"]


async def test_same_user_analyses_are_sequential(settings):
    gw = FakeGateway(images={"u1": IMAGE, "u2": IMAGE})
    r, _, _ = runner(settings, FakeLLM(make_extraction(), make_verdict(), delay=0.05), gw)
    await asyncio.gather(
        r.handle(inbound("MM1", media=(MediaRef("u1", "image/jpeg"),)), USER),
        r.handle(inbound("MM2", media=(MediaRef("u2", "image/jpeg"),)), USER),
    )
    assert kinds(gw) == ["ack", "progress", "final"] * 2


async def test_logs_carry_analysis_context(settings):
    r, _, _ = runner(settings)
    # merge_contextvars makes the bound context (analysis_id, ...) visible to the capture.
    with structlog.testing.capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        await r.handle(inbound(), USER)
    scoped = [e for e in logs if e["event"].startswith(("analysis.", "graph."))]
    assert scoped and all(e.get("analysis_id") and e.get("message_sid") == "MM1" for e in scoped)
    assert any(e["event"] == "analysis.completed" and e["label"] == "sospetto" for e in logs)
    assert NUMBER not in str(logs)


async def test_langsmith_config_passed(settings):
    seen = {}

    class SpyGraph:
        async def astream(self, state, config=None, stream_mode=None):
            seen.update(config)
            yield ("values", {**state, "reply_text": "ok"})

    await AnalysisRunner(SpyGraph(), FakeGateway(images={"u1": IMAGE}), settings).handle(
        inbound(), USER
    )
    assert seen["run_name"] == "phishing-analysis"
    assert seen["metadata"]["message_sid"] == "MM1" and seen["metadata"]["user_name"] == "Davide"
    assert NUMBER not in str(seen)


def test_deduper_ttl():
    now = [0.0]
    d = MessageDeduper(ttl_seconds=10, clock=lambda: now[0])
    assert d.first_time("MM1") and not d.first_time("MM1")
    now[0] = 11
    assert d.first_time("MM1")
