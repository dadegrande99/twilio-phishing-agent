"""Inbound side of the WhatsApp Cloud API (Meta): signature validation and payload parsing.

See docs/meta-whatsapp.md.
"""

import hashlib
import hmac
from dataclasses import dataclass

from app.channels.models import InboundMessage, MediaRef
from app.logging import get_logger

log = get_logger(__name__)

# Message types that carry a media object with `id` and `mime_type`. Only images are
# analysed; audio/video keep their mime so the runner answers "only images or text".
# Stickers and documents are reported as unsupported even if their mime is image/*.
_MEDIA_TYPES = ("image", "audio", "video")
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
    return hmac.compare_digest(header.encode(), expected.encode())


def _normalize_sender(raw: str) -> str:
    # Meta sends the number without '+'; anything non-numeric is left as is and
    # will simply not match the whitelist.
    return f"+{raw}" if raw.isdigit() else raw


class _Malformed(Exception):
    pass


def _str(value, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise _Malformed(type(value).__name__)
    return value


def _dict(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _Malformed(type(value).__name__)
    return value


def _parse_message(message: dict, names: dict[str, str]) -> InboundMessage | None:
    kind = _str(message.get("type"))
    if kind in _IGNORED_TYPES:
        return None
    sender = str(message.get("from", ""))
    body, media = "", []
    if kind == "text":
        body = _str(_dict(message.get("text")).get("body"))
    elif kind in _MEDIA_TYPES:
        obj = _dict(message.get(kind))
        media = [MediaRef(locator=_str(obj.get("id")), content_type=_str(obj.get("mime_type")))]
        body = _str(obj.get("caption"))
    else:
        media = [MediaRef(locator="", content_type=f"unsupported/{kind or 'unknown'}")]
    return InboundMessage(
        message_sid=_str(message.get("id")),
        from_number=_normalize_sender(sender),
        body=body.strip(),
        media=media,
        profile_name=names.get(sender),
    )


def _parse_status(status: dict) -> MetaStatus:
    errors = status.get("errors")
    error = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
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


def _list(value) -> list:
    return value if isinstance(value, list) else []


def parse_meta_webhook(payload) -> MetaEvents:
    """Never raises: malformed elements are skipped (and counted), valid ones kept."""
    events = MetaEvents(messages=[], statuses=[])
    malformed = 0
    for value in _values(payload):
        names = {}
        for contact in _list(value.get("contacts")):
            if isinstance(contact, dict) and isinstance(contact.get("profile"), dict):
                names[str(contact.get("wa_id"))] = contact["profile"].get("name")
        for message in _list(value.get("messages")):
            try:
                if not isinstance(message, dict):
                    raise _Malformed(type(message).__name__)
                if parsed := _parse_message(message, names):
                    events.messages.append(parsed)
            except _Malformed:
                malformed += 1
        for status in _list(value.get("statuses")):
            if isinstance(status, dict):
                events.statuses.append(_parse_status(status))
            else:
                malformed += 1
    if malformed:
        log.warning("meta.payload.malformed", skipped=malformed)
    return events
