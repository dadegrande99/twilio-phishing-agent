import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults(settings):
    assert settings.anthropic_model == "claude-haiku-5-5"
    assert settings.validate_twilio_signature is True
    assert settings.twilio_whatsapp_from == "whatsapp:+14155238886"


def test_public_base_url_trailing_slash_removed():
    s = Settings(
        _env_file=None,
        anthropic_api_key="k",
        twilio_account_sid="AC",
        twilio_auth_token="t",
        public_base_url="https://x.ngrok-free.app/",
    )
    assert s.public_base_url == "https://x.ngrok-free.app"


def test_missing_required_key_fails(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            twilio_account_sid="AC",
            twilio_auth_token="t",
            public_base_url="https://x",
        )
