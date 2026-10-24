from twilio.request_validator import RequestValidator

from app.channels.twilio_security import (
    InboundMessage,
    MediaRef,
    is_valid_signature,
    parse_inbound,
    public_url,
)

URL = "https://test.example/webhooks/twilio/whatsapp"
PARAMS = {
    "MessageSid": "MM1",
    "From": "whatsapp:+393331234567",
    "Body": " è vero? ",
    "NumMedia": "1",
    "MediaUrl0": "https://api.twilio.com/m/ME1",
    "MediaContentType0": "image/jpeg",
    "ProfileName": "Davide",
}


def test_public_url(settings):
    assert public_url(settings, "/webhooks/twilio/whatsapp") == URL


def test_valid_signature():
    sig = RequestValidator("tok").compute_signature(URL, PARAMS)
    assert is_valid_signature("tok", URL, PARAMS, sig)


def test_signature_wrong_url_fails():
    sig = RequestValidator("tok").compute_signature(URL, PARAMS)
    assert not is_valid_signature(
        "tok", "https://other.example/webhooks/twilio/whatsapp", PARAMS, sig
    )


def test_signature_wrong_token_fails():
    sig = RequestValidator("tok").compute_signature(URL, PARAMS)
    assert not is_valid_signature("other", URL, PARAMS, sig)


def test_missing_signature_fails():
    assert not is_valid_signature("tok", URL, PARAMS, None)


def test_parse_inbound():
    assert parse_inbound(PARAMS) == InboundMessage(
        "MM1",
        "+393331234567",
        "è vero?",
        [MediaRef("https://api.twilio.com/m/ME1", "image/jpeg")],
        "Davide",
    )


def test_parse_text_only():
    m = parse_inbound(
        {"MessageSid": "SM1", "From": "whatsapp:+39333", "Body": "ciao", "NumMedia": "0"}
    )
    assert m.media == [] and m.body == "ciao" and m.profile_name is None


def test_parse_tolerates_missing_fields():
    m = parse_inbound({"MessageSid": "SM2", "From": "whatsapp:+39333"})
    assert m.body == "" and m.media == []
