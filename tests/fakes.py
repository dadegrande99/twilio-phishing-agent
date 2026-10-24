"""Test doubles and builders shared by the test suite."""

import asyncio

from app.agent.schemas import Extraction, Verdict


def make_extraction(**overrides) -> Extraction:
    data = {
        "content_kind": "messaggio_da_analizzare",
        "channel": "sms",
        "sender": None,
        "claimed_brand": None,
        "text": "testo",
        "urls": [],
        "phone_numbers": [],
        "call_to_action": None,
        "summary": "riassunto",
    }
    data.update(overrides)
    return Extraction(**data)


def make_verdict(**overrides) -> Verdict:
    data = {
        "label": "sospetto",
        "confidence": "media",
        "red_flags": ["segnale sospetto"],
        "legit_signals": ["segnale legittimo"],
        "recommended_actions": ["azione consigliata"],
        "explanation": "spiegazione",
    }
    data.update(overrides)
    return Verdict(**data)


class FakeLLM:
    """AnalysisLLM double: returns fixed results and records which calls happened."""

    def __init__(
        self,
        extraction: Extraction,
        verdict: Verdict,
        *,
        extract_error: Exception | None = None,
        judge_error: Exception | None = None,
        delay: float = 0,
    ):
        self.extraction = extraction
        self.verdict = verdict
        self.extract_error = extract_error
        self.judge_error = judge_error
        self.delay = delay
        self.calls: list[str] = []
        self.extract_args: list[tuple] = []

    async def extract(self, caption, images) -> Extraction:
        self.calls.append("extract")
        self.extract_args.append((caption, images))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.extract_error:
            raise self.extract_error
        return self.extraction

    async def judge(self, extraction, caption) -> Verdict:
        self.calls.append("judge")
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.judge_error:
            raise self.judge_error
        return self.verdict
