import pytest
import structlog

from app.agent.formatting import HELP_TEXT, format_progress, format_verdict
from app.agent.graph import build_graph, run_analysis
from app.agent.llm import LLMRefusalError
from app.agent.schemas import ImageInput
from tests.fakes import FakeLLM, make_extraction, make_verdict


def state(caption="x", images=None):
    return {"analysis_id": "a1", "caption": caption, "images": images or []}


async def test_message_goes_through_judge_and_format():
    extraction = make_extraction(channel="sms", claimed_brand="Poste")
    llm = FakeLLM(extraction, make_verdict(label="phishing"))
    events = []

    async def on_event(event):
        events.append(event)

    final = await run_analysis(build_graph(llm), state(), on_event=on_event)
    assert llm.calls == ["extract", "judge"]
    assert final["reply_text"] == format_verdict(make_verdict(label="phishing"))
    assert events == [{"type": "extracted", "progress_text": format_progress(extraction)}]


async def test_images_and_caption_reach_llm():
    llm = FakeLLM(make_extraction(), make_verdict())
    image = ImageInput("image/png", "QQ==")
    await run_analysis(build_graph(llm), state(caption="è vero?", images=[image]))
    assert llm.extract_args == [("è vero?", [image])]


async def test_conversation_routes_to_help_without_judge():
    llm = FakeLLM(make_extraction(content_kind="conversazione"), make_verdict())
    events = []

    async def on_event(event):
        events.append(event)

    final = await run_analysis(build_graph(llm), state(caption="ciao"), on_event=on_event)
    assert llm.calls == ["extract"]
    assert final["reply_text"] == HELP_TEXT
    assert "verdict" not in final
    assert events == []


async def test_node_timing_logged():
    with structlog.testing.capture_logs() as logs:
        await run_analysis(build_graph(FakeLLM(make_extraction(), make_verdict())), state())
    done = [entry["node"] for entry in logs if entry["event"] == "graph.node.completed"]
    assert done == ["extract", "judge", "format"]
    assert any(entry["event"] == "verdict.result" for entry in logs)


async def test_extraction_text_not_logged_by_default():
    llm = FakeLLM(make_extraction(text="SEGRETO"), make_verdict())
    with structlog.testing.capture_logs() as logs:
        await run_analysis(build_graph(llm), state())
    assert "SEGRETO" not in str(logs)


async def test_llm_error_propagates():
    llm = FakeLLM(make_extraction(), make_verdict(), judge_error=LLMRefusalError("cyber"))
    with pytest.raises(LLMRefusalError):
        await run_analysis(build_graph(llm), state())
