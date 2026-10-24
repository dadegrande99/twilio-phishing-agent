"""LangGraph analysis graph.

    START ─▶ extract ─┬─ conversazione ──────▶ help ─────────▶ END
                      └─ messaggio ─▶ judge ─▶ format ───────▶ END

`extract` emits a custom stream event ("extracted") that the runner turns into
the WhatsApp progress message.
"""

import time
from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agent.formatting import HELP_TEXT, format_progress, format_verdict
from app.agent.llm import AnalysisLLM
from app.agent.state import AnalysisState
from app.logging import get_logger

log = get_logger(__name__)

NodeFn = Callable[[AnalysisState, RunnableConfig], Awaitable[dict]]


def _timed(name: str, fn: NodeFn) -> NodeFn:
    async def node(state: AnalysisState, config: RunnableConfig) -> dict:
        log.info("graph.node.started", node=name)
        start = time.perf_counter()
        result = await fn(state, config)
        log.info("graph.node.completed", node=name, ms=round((time.perf_counter() - start) * 1000))
        return result

    return node


def build_graph(llm: AnalysisLLM):
    async def extract(state: AnalysisState, config: RunnableConfig) -> dict:
        extraction = await llm.extract(state.get("caption"), state.get("images", []))
        log_content = config.get("configurable", {}).get("log_content", False)
        log.info(
            "extraction.result",
            content_kind=extraction.content_kind,
            channel=extraction.channel,
            claimed_brand=extraction.claimed_brand,
            n_urls=len(extraction.urls),
            n_phones=len(extraction.phone_numbers),
            **({"text": extraction.text, "urls": extraction.urls} if log_content else {}),
        )
        if extraction.content_kind == "messaggio_da_analizzare":
            get_stream_writer()(
                {"type": "extracted", "progress_text": format_progress(extraction)}
            )
        return {"extraction": extraction}

    async def judge(state: AnalysisState, config: RunnableConfig) -> dict:
        verdict = await llm.judge(state["extraction"], state.get("caption"))
        log.info(
            "verdict.result",
            label=verdict.label,
            confidence=verdict.confidence,
            n_red_flags=len(verdict.red_flags),
        )
        return {"verdict": verdict}

    async def format_reply(state: AnalysisState, config: RunnableConfig) -> dict:
        return {"reply_text": format_verdict(state["verdict"])}

    async def help_reply(state: AnalysisState, config: RunnableConfig) -> dict:
        return {"reply_text": HELP_TEXT}

    def route(state: AnalysisState) -> str:
        return "help" if state["extraction"].content_kind == "conversazione" else "judge"

    graph = StateGraph(AnalysisState)
    graph.add_node("extract", _timed("extract", extract))
    graph.add_node("judge", _timed("judge", judge))
    graph.add_node("format", _timed("format", format_reply))
    graph.add_node("help", _timed("help", help_reply))
    graph.add_edge(START, "extract")
    graph.add_conditional_edges("extract", route, {"help": "help", "judge": "judge"})
    graph.add_edge("judge", "format")
    graph.add_edge("format", END)
    graph.add_edge("help", END)
    return graph.compile()


async def run_analysis(
    graph,
    state: AnalysisState,
    *,
    config: dict | None = None,
    on_event: Callable[[dict], Awaitable[None]] | None = None,
) -> AnalysisState:
    """Run the graph, forwarding custom events to `on_event`; returns the final state."""
    final: AnalysisState = state
    async for mode, chunk in graph.astream(state, config=config, stream_mode=["custom", "values"]):
        if mode == "custom":
            if on_event is not None:
                await on_event(chunk)
        else:
            final = chunk
    return final
