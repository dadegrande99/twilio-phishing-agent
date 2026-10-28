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


def test_meta_channel_requires_meta_credentials():
    s = Settings(_env_file=None, anthropic_api_key="k", public_base_url="https://x", channel="meta")
    with pytest.raises(ValueError, match="META_ACCESS_TOKEN"):
        s.check_channel_credentials()


def test_settings_without_channel_credentials_still_load():
    # Tools that only need Claude (evals) must not require WhatsApp credentials.
    s = Settings(_env_file=None, anthropic_api_key="k", public_base_url="https://x", channel="meta")
    assert s.anthropic_model == "claude-haiku-5-5"


def test_app_refuses_to_start_without_channel_credentials(tmp_path):
    from app.main import create_app
    from tests.fakes import FakeLLM, make_extraction, make_verdict

    s = Settings(_env_file=None, anthropic_api_key="k", public_base_url="https://x", channel="meta")
    with pytest.raises(ValueError, match="CHANNEL=meta requires"):
        create_app(s, llm=FakeLLM(make_extraction(), make_verdict()))


def test_meta_channel_does_not_require_twilio(meta_settings):
    assert meta_settings.channel == "meta" and meta_settings.twilio_account_sid == ""
    assert meta_settings.meta_graph_version == "v26.0"
    assert meta_settings.validate_meta_signature is True


def test_twilio_channel_requires_twilio_credentials():
    s = Settings(_env_file=None, anthropic_api_key="k", public_base_url="https://x")
    with pytest.raises(ValueError, match="TWILIO_ACCOUNT_SID"):
        s.check_channel_credentials()


def test_default_channel_is_twilio(settings):
    assert settings.channel == "twilio"


def test_default_inbound_mode_is_polling():
    s = Settings(
        _env_file=None, anthropic_api_key="k", twilio_account_sid="AC", twilio_auth_token="t"
    )
    assert s.inbound_mode == "polling" and s.poll_interval_seconds == 5
    s.check_channel_credentials()  # polling needs no PUBLIC_BASE_URL


def test_meta_with_polling_refused(meta_settings):
    meta_settings.inbound_mode = "polling"
    with pytest.raises(ValueError, match="INBOUND_MODE=polling"):
        meta_settings.check_channel_credentials()


def test_webhook_mode_requires_public_url():
    s = Settings(
        _env_file=None, anthropic_api_key="k", twilio_account_sid="AC", twilio_auth_token="t",
        inbound_mode="webhook",
    )
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        s.check_channel_credentials()
