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


class FakeGateway:
    """MessagingGateway double: records sent messages, serves images by URL."""

    def __init__(
        self,
        *,
        images: dict | None = None,
        fail_kinds: set[str] | None = None,
        send_delay: float = 0,
    ):
        self.images = images or {}
        self.fail_kinds = fail_kinds or set()
        self.send_delay = send_delay
        self.sent: list[tuple[str, str, str]] = []
        self.acked: list[str] = []

    async def send(self, to: str, body: str, kind: str) -> str | None:
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        if kind in self.fail_kinds:
            return None
        self.sent.append((to, body, kind))
        return f"SM{len(self.sent)}"

    async def acknowledge(self, to: str, message_id: str, ack_text: str) -> None:
        self.acked.append(message_id)
        await self.send(to, ack_text, "ack")

    async def download_image(self, media):
        result = self.images[media.locator]
        if isinstance(result, Exception):
            raise result
        return result


def text_msg(wamid: str, from_: str, body: str) -> dict:
    return {"from": from_, "id": wamid, "timestamp": "1700000000", "type": "text",
            "text": {"body": body}}


def meta_payload(messages=None, statuses=None, contacts=None) -> dict:
    """A WhatsApp Cloud API webhook payload with the real nesting."""
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "PNID"},
    }
    if contacts is not None:
        value["contacts"] = contacts
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}],
    }
