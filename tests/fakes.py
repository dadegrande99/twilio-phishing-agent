"""Test doubles and builders shared by the test suite."""

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
