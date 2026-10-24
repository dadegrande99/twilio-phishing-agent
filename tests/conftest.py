import json

import pytest

from app.config import Settings

WHITELISTED_NUMBER = "+393331234567"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Keep real environment variables (e.g. from a developer's .env) out of the tests."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    whitelist_path = tmp_path / "whitelist.json"
    whitelist_path.write_text(
        json.dumps({"allowed_numbers": [{"number": WHITELISTED_NUMBER, "name": "Davide"}]})
    )
    return Settings(
        _env_file=None,
        anthropic_api_key="test-key",
        twilio_account_sid="ACtest",
        twilio_auth_token="test-token",
        public_base_url="https://test.example",
        whitelist_path=whitelist_path,
    )
