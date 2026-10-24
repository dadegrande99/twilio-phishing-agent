"""Validation of images received from users."""

import base64
from typing import Literal

from app.agent.schemas import ImageInput

ALLOWED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
# 3.75 MB raw stays under the 5 MB per-image limit of the Claude API once base64-encoded.
MAX_IMAGE_BYTES = 3_750_000

RejectReason = Literal["unsupported_type", "too_large", "download_failed"]


class MediaRejected(Exception):
    def __init__(self, reason: RejectReason):
        super().__init__(reason)
        self.reason: RejectReason = reason


def normalize_content_type(content_type: str) -> str:
    return content_type.split(";", 1)[0].strip().lower()


def validate_image(content_type: str, data: bytes) -> ImageInput:
    mime = normalize_content_type(content_type)
    if mime not in ALLOWED_IMAGE_TYPES:
        raise MediaRejected("unsupported_type")
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaRejected("too_large")
    return ImageInput(mime=mime, b64=base64.standard_b64encode(data).decode("ascii"))
