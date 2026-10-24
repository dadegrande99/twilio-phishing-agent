"""Twilio webhooks: inbound WhatsApp messages and outbound message status updates.

The inbound webhook only runs fast checks and answers immediately with empty
TwiML (Twilio gives up after 15 s); the analysis runs as a background task and
talks to the user through the Twilio REST API. See docs/twilio.md §4.
"""

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.channels.twilio_security import is_valid_signature, parse_inbound, public_url
from app.logging import get_logger, mask_number

log = get_logger(__name__)

router = APIRouter()

WEBHOOK_PATH = "/webhooks/twilio/whatsapp"
STATUS_PATH = "/webhooks/twilio/status"
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _twiml() -> Response:
    return Response(content=EMPTY_TWIML, media_type="application/xml")


async def _verified_params(request: Request, path: str) -> dict[str, str] | None:
    """Form params if the Twilio signature is valid (or checking is disabled), else None."""
    params = {key: str(value) for key, value in (await request.form()).items()}
    settings = request.app.state.settings
    if not settings.validate_twilio_signature:
        return params
    expected_url = public_url(settings, path)
    if is_valid_signature(
        settings.twilio_auth_token.get_secret_value(),
        expected_url,
        params,
        request.headers.get("X-Twilio-Signature"),
    ):
        return params
    log.warning(
        "webhook.signature_invalid",
        path=path,
        expected_url=expected_url,
        hint="PUBLIC_BASE_URL must match the URL configured in the Twilio console",
    )
    return None


@router.post(WEBHOOK_PATH)
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks) -> Response:
    params = await _verified_params(request, WEBHOOK_PATH)
    if params is None:
        return Response(status_code=403)

    inbound = parse_inbound(params)
    log.info(
        "webhook.received",
        message_sid=inbound.message_sid,
        num_media=len(inbound.media),
        has_body=bool(inbound.body),
    )

    state = request.app.state
    user = state.whitelist.lookup(inbound.from_number)
    if user is None:
        # Never answer numbers outside the whitelist, not even with an error.
        log.info("webhook.not_whitelisted", user_masked=mask_number(inbound.from_number))
        return _twiml()

    if not state.deduper.first_time(inbound.message_sid):
        log.info("webhook.duplicate", message_sid=inbound.message_sid)
        return _twiml()

    background_tasks.add_task(state.runner.handle, inbound, user)
    return _twiml()


@router.post(STATUS_PATH)
async def status_callback(request: Request) -> Response:
    params = await _verified_params(request, STATUS_PATH)
    if params is None:
        return Response(status_code=403)
    log.info(
        "twilio.status.updated",
        message_sid=params.get("MessageSid"),
        status=params.get("MessageStatus"),
        error_code=params.get("ErrorCode") or None,
        to=mask_number(params.get("To", "")),
    )
    return Response(status_code=204)
