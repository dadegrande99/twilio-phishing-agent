import base64

import pytest

from app.media import MAX_IMAGE_BYTES, MediaRejected, validate_image


def test_accepts_jpeg():
    img = validate_image("image/jpeg", b"\xff\xd8data")
    assert img.mime == "image/jpeg" and base64.b64decode(img.b64) == b"\xff\xd8data"


def test_content_type_params_ignored():
    assert validate_image("IMAGE/PNG; charset=binary", b"x").mime == "image/png"


def test_rejects_audio():
    with pytest.raises(MediaRejected) as e:
        validate_image("audio/ogg", b"x")
    assert e.value.reason == "unsupported_type"


def test_rejects_too_large():
    with pytest.raises(MediaRejected) as e:
        validate_image("image/png", b"x" * (MAX_IMAGE_BYTES + 1))
    assert e.value.reason == "too_large"
