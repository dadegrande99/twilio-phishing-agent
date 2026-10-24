import json

import pytest
import structlog

from app.logging import configure_logging, get_logger, mask_number


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+393331234567", "+39***4567"),
        ("whatsapp:+393331234567", "+39***4567"),
        ("123", "***"),
    ],
)
def test_mask_number(raw, expected):
    assert mask_number(raw) == expected


def test_json_file_contains_bound_context(tmp_path):
    log_file = tmp_path / "app.jsonl"
    configure_logging("INFO", "json", log_file)
    with structlog.contextvars.bound_contextvars(analysis_id="abc123"):
        get_logger("test").info("analysis.started", wait_ms=5)
    line = json.loads(log_file.read_text().strip().splitlines()[-1])
    assert line["event"] == "analysis.started"
    assert line["analysis_id"] == "abc123"
    assert line["wait_ms"] == 5
    assert "timestamp" in line and line["level"] == "info"


def test_debug_level_keeps_sdk_loggers_quiet(tmp_path):
    # At DEBUG the anthropic SDK would log full request bodies (base64 images, captions).
    import logging

    configure_logging("DEBUG", "json", tmp_path / "app.jsonl")
    for name in ("anthropic", "langsmith", "httpx", "twilio"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING
