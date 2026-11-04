import hashlib
import hmac

from app.channels.meta_security import (
    MetaEvents,
    MetaStatus,
    is_valid_meta_signature,
    parse_meta_webhook,
)
from app.channels.models import InboundMessage, MediaRef
from tests.fakes import meta_payload, text_msg

NUMBER_RAW = "393331234567"


def sign(body: bytes, secret: str = "app-secret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature():
    body = b'{"a":1}'
    assert is_valid_meta_signature("app-secret", body, sign(body))


def test_tampered_body_fails():
    assert not is_valid_meta_signature("app-secret", b'{"a":2}', sign(b'{"a":1}'))


def test_wrong_secret_or_missing_header_fails():
    body = b'{"a":1}'
    assert not is_valid_meta_signature("other", body, sign(body))
    assert not is_valid_meta_signature("app-secret", body, None)
    assert not is_valid_meta_signature("app-secret", body, "garbage")


def test_text_message():
    events = parse_meta_webhook(
        meta_payload(
            messages=[text_msg("wamid.1", NUMBER_RAW, " ciao ")],
            contacts=[{"wa_id": NUMBER_RAW, "profile": {"name": "Davide"}}],
        )
    )
    assert events.messages == [InboundMessage("wamid.1", "+393331234567", "ciao", [], "Davide")]
    assert events.statuses == []


def test_image_with_caption():
    events = parse_meta_webhook(
        meta_payload(
            messages=[
                {
                    "from": NUMBER_RAW,
                    "id": "wamid.2",
                    "type": "image",
                    "image": {"id": "MEDIA1", "mime_type": "image/jpeg", "sha256": "x",
                              "caption": "è vero?"},
                }
            ]
        )
    )
    message = events.messages[0]
    assert message.media == [MediaRef("MEDIA1", "image/jpeg")]
    assert message.body == "è vero?"
    assert message.profile_name is None


def test_image_without_caption():
    events = parse_meta_webhook(
        meta_payload(messages=[{"from": NUMBER_RAW, "id": "w", "type": "image",
                                "image": {"id": "M", "mime_type": "image/png"}}])
    )
    assert events.messages[0].body == ""


def test_reaction_ignored():
    events = parse_meta_webhook(
        meta_payload(messages=[{"from": NUMBER_RAW, "id": "w", "type": "reaction",
                                "reaction": {"message_id": "wamid.out", "emoji": "👍"}}])
    )
    assert events.messages == []


def test_location_is_unsupported():
    events = parse_meta_webhook(
        meta_payload(messages=[{"from": NUMBER_RAW, "id": "w", "type": "location",
                                "location": {"latitude": 1, "longitude": 2}}])
    )
    assert events.messages[0].media == [MediaRef("", "unsupported/location")]


def test_audio_keeps_mime():
    events = parse_meta_webhook(
        meta_payload(messages=[{"from": NUMBER_RAW, "id": "w", "type": "audio",
                                "audio": {"id": "A1", "mime_type": "audio/ogg; codecs=opus"}}])
    )
    assert events.messages[0].media == [MediaRef("A1", "audio/ogg; codecs=opus")]


def test_non_numeric_sender_kept_as_is():
    events = parse_meta_webhook(meta_payload(messages=[text_msg("w", "user.abc", "x")]))
    assert events.messages[0].from_number == "user.abc"


def test_multiple_messages_in_one_payload():
    events = parse_meta_webhook(
        meta_payload(messages=[text_msg("w1", NUMBER_RAW, "a"), text_msg("w2", NUMBER_RAW, "b")])
    )
    assert [m.message_sid for m in events.messages] == ["w1", "w2"]


def test_statuses_parsed():
    events = parse_meta_webhook(
        meta_payload(
            statuses=[
                {"id": "wamid.9", "status": "failed", "recipient_id": NUMBER_RAW,
                 "errors": [{"code": 131047, "title": "Re-engagement message"}]},
                {"id": "wamid.8", "status": "delivered", "recipient_id": NUMBER_RAW},
            ]
        )
    )
    assert events.statuses == [
        MetaStatus("wamid.9", "failed", NUMBER_RAW, 131047, "Re-engagement message"),
        MetaStatus("wamid.8", "delivered", NUMBER_RAW, None, None),
    ]
    assert events.messages == []


def test_garbage_payload():
    for payload in ({}, {"entry": "x"}, {"entry": [{"changes": [{"value": None}]}]}, []):
        assert parse_meta_webhook(payload) == MetaEvents([], [])


def test_malformed_elements_skipped_valid_siblings_kept():
    payload = meta_payload(
        messages=[
            5,
            {"from": NUMBER_RAW, "id": "bad1", "type": "text", "text": "hi"},
            {"from": NUMBER_RAW, "id": "bad2", "type": "text", "text": {"body": 5}},
            {"from": NUMBER_RAW, "id": "bad3", "type": "image", "image": {"id": "M", "caption": 7}},
            text_msg("good", NUMBER_RAW, "ok"),
        ],
        statuses=[{"id": "s1", "status": "failed", "errors": "boom"},
                  {"id": "s2", "status": "failed", "errors": {"code": 1}}],
        contacts=5,
    )
    events = parse_meta_webhook(payload)
    assert [m.message_sid for m in events.messages] == ["good"]
    assert [s.message_id for s in events.statuses] == ["s1", "s2"]


def test_non_list_messages_ignored():
    value_payload = meta_payload()
    value_payload["entry"][0]["changes"][0]["value"]["messages"] = 5
    value_payload["entry"][0]["changes"][0]["value"]["statuses"] = 5
    assert parse_meta_webhook(value_payload) == MetaEvents([], [])


def test_sticker_and_document_are_unsupported():
    events = parse_meta_webhook(meta_payload(messages=[
        {"from": NUMBER_RAW, "id": "s", "type": "sticker", "sticker": {"id": "S", "mime_type": "image/webp"}},
        {"from": NUMBER_RAW, "id": "d", "type": "document", "document": {"id": "D", "mime_type": "image/png"}},
    ]))
    assert [m.media for m in events.messages] == [
        [MediaRef("", "unsupported/sticker")], [MediaRef("", "unsupported/document")]
    ]


def test_non_ascii_signature_header_is_invalid_not_error():
    assert not is_valid_meta_signature("app-secret", b"{}", "sha256=é")
