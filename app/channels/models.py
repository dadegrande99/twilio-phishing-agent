"""Channel-independent message types shared by Twilio and Meta."""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.agent.schemas import ImageInput


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


MessageKind = Literal["ack", "progress", "final", "help", "error"]


class MessagingGateway(Protocol):
    async def send(self, to: str, body: str, kind: MessageKind) -> str | None:
        """Send a WhatsApp message; returns the provider message id, or None on failure. Never raises."""
        ...

    async def download_image(self, media: MediaRef) -> ImageInput:
        """Download and validate an inbound image; raises MediaRejected."""
        ...

    async def acknowledge(self, to: str, message_id: str, ack_text: str) -> None:
        """Tell the user their message arrived, the channel's way. Never raises."""
        ...
