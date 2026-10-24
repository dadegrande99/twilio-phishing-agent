"""Channel-independent message types shared by Twilio and Meta."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaRef:
    """A media attachment: `locator` is the Twilio media URL or the Meta media id."""

    locator: str
    content_type: str


@dataclass(frozen=True)
class InboundMessage:
    message_sid: str
    from_number: str
    body: str
    media: list[MediaRef]
    profile_name: str | None
