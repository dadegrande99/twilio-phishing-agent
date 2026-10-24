from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-haiku-5-5"

    twilio_account_sid: str
    twilio_auth_token: SecretStr
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    public_base_url: str
    validate_twilio_signature: bool = True
    whitelist_path: Path = Path("config/whitelist.json")
    analysis_timeout_seconds: float = 90
    debug: bool = False

    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"
    log_file: Path | None = None
    log_content: bool = False

    @field_validator("public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")
