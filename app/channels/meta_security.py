"""Inbound side of the WhatsApp Cloud API (Meta): signature validation and payload parsing.

See docs/meta-whatsapp.md.
"""

import hashlib
import hmac
from dataclasses import dataclass

from app.channels.models import InboundMessage, MediaRef

# Message types that carry a downloadable media object with `id` and `mime_type`.
_MEDIA_TYPES = ("image", "audio", "video", "document", "sticker")
# Message types that never deserve a reply (e.g. an emoji reaction to our verdict).
_IGNORED_TYPES = ("reaction",)


@dataclass(frozen=True)
class MetaStatus:
    message_id: str
    status: str
    recipient: str
    error_code: int | None
    error_title: str | None


@dataclass
class MetaEvents:
    messages: list[InboundMessage]
    statuses: list[MetaStatus]


def is_valid_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app_secret, raw body)."""
    if not header:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header, expected)


def _normalize_sender(raw: str) -> str:
    # Meta sends the number without '+'; anything non-numeric is left as is and
    # will simply not match the whitelist.
    return f"+{raw}" if raw.isdigit() else raw


def _parse_message(message: dict, names: dict[str, str]) -> InboundMessage | None:
    kind = message.get("type", "")
    if kind in _IGNORED_TYPES:
        return None
    sender = str(message.get("from", ""))
    body, media = "", []
    if kind == "text":
        body = (message.get("text") or {}).get("body", "")
    elif kind in _MEDIA_TYPES:
        obj = message.get(kind) or {}
        media = [MediaRef(locator=obj.get("id", ""), content_type=obj.get("mime_type", ""))]
        body = obj.get("caption", "")
    else:
        media = [MediaRef(locator="", content_type=f"unsupported/{kind or 'unknown'}")]
    return InboundMessage(
        message_sid=message.get("id", ""),
        from_number=_normalize_sender(sender),
        body=(body or "").strip(),
        media=media,
        profile_name=names.get(sender),
    )


def _parse_status(status: dict) -> MetaStatus:
    error = (status.get("errors") or [{}])[0]
    return MetaStatus(
        message_id=status.get("id", ""),
        status=status.get("status", ""),
        recipient=str(status.get("recipient_id", "")),
        error_code=error.get("code"),
        error_title=error.get("title"),
    )


def _values(payload) -> list[dict]:
    """All `entry[].changes[].value` dicts, skipping anything malformed."""
    values = []
    entries = payload.get("entry") if isinstance(payload, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        for change in changes if isinstance(changes, list) else []:
            value = change.get("value") if isinstance(change, dict) else None
            if isinstance(value, dict):
                values.append(value)
    return values


def parse_meta_webhook(payload) -> MetaEvents:
    events = MetaEvents(messages=[], statuses=[])
    for value in _values(payload):
        names = {
            str(c.get("wa_id")): (c.get("profile") or {}).get("name")
            for c in value.get("contacts") or []
            if isinstance(c, dict)
        }
        for message in value.get("messages") or []:
            if isinstance(message, dict) and (parsed := _parse_message(message, names)):
                events.messages.append(parsed)
        for status in value.get("statuses") or []:
            if isinstance(status, dict):
                events.statuses.append(_parse_status(status))
    return events
