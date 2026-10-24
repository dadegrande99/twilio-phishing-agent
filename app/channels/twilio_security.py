"""Inbound side of Twilio: signature validation and webhook payload parsing.

See docs/twilio.md (§3 payload, §5 signature).
"""

from collections.abc import Mapping

from twilio.request_validator import RequestValidator

from app.channels.models import InboundMessage, MediaRef
from app.config import Settings

__all__ = ["InboundMessage", "MediaRef", "is_valid_signature", "parse_inbound", "public_url"]


def public_url(settings: Settings, path: str) -> str:
    """URL as Twilio sees it (behind ngrok FastAPI only sees localhost)."""
    return settings.public_base_url + path


def is_valid_signature(
    auth_token: str, url: str, params: Mapping[str, str], signature: str | None
) -> bool:
    if not signature:
        return False
    return RequestValidator(auth_token).validate(url, dict(params), signature)


def parse_inbound(params: Mapping[str, str]) -> InboundMessage:
    try:
        num_media = int(params.get("NumMedia", "0") or 0)
    except ValueError:
        num_media = 0
    media = [
        MediaRef(locator=params[f"MediaUrl{i}"], content_type=params.get(f"MediaContentType{i}", ""))
        for i in range(num_media)
        if f"MediaUrl{i}" in params
    ]
    return InboundMessage(
        message_sid=params.get("MessageSid", ""),
        from_number=params.get("From", "").removeprefix("whatsapp:"),
        body=params.get("Body", "").strip(),
        media=media,
        profile_name=params.get("ProfileName") or None,
    )
