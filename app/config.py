from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: SecretStr
    anthropic_model: str = "claude-haiku-5-5"

    # Which WhatsApp provider this process talks to.
    channel: Literal["twilio", "meta"] = "twilio"

    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    meta_access_token: SecretStr = SecretStr("")
    meta_phone_number_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    meta_verify_token: SecretStr = SecretStr("")
    meta_graph_version: str = "v26.0"
    validate_meta_signature: bool = True

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

    def check_channel_credentials(self) -> None:
        """Raise if the active channel's credentials are missing (called at app startup).

        Not a model validator: tools that only need Claude (evals) must load settings
        without WhatsApp credentials.
        """
        required = {
            "twilio": {
                "TWILIO_ACCOUNT_SID": self.twilio_account_sid,
                "TWILIO_AUTH_TOKEN": self.twilio_auth_token.get_secret_value(),
            },
            "meta": {
                "META_ACCESS_TOKEN": self.meta_access_token.get_secret_value(),
                "META_PHONE_NUMBER_ID": self.meta_phone_number_id,
                "META_APP_SECRET": self.meta_app_secret.get_secret_value(),
                "META_VERIFY_TOKEN": self.meta_verify_token.get_secret_value(),
            },
        }[self.channel]
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"CHANNEL={self.channel} requires: {', '.join(missing)}")
