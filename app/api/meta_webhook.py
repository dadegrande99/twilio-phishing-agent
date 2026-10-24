"""WhatsApp Cloud API (Meta) webhook: verification handshake, messages and statuses.

Meta retries deliveries that don't get a 200, so every validly signed payload is
answered 200 immediately; the analysis runs as a background task. See
docs/meta-whatsapp.md.
"""

import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import PlainTextResponse

from app.channels.meta_security import is_valid_meta_signature, parse_meta_webhook
from app.logging import get_logger, mask_number

log = get_logger(__name__)

router = APIRouter()

WEBHOOK_PATH = "/webhooks/meta"


@router.get(WEBHOOK_PATH)
async def verify(request: Request) -> Response:
    """Handshake done by Meta when you save the callback URL in the App Dashboard."""
    params = request.query_params
    expected = request.app.state.settings.meta_verify_token.get_secret_value()
    token = params.get("hub.verify_token", "")
    if params.get("hub.mode") == "subscribe" and hmac.compare_digest(token, expected):
        log.info("meta.verified")
        return PlainTextResponse(params.get("hub.challenge", ""))
    log.warning("meta.verify_failed", mode=params.get("hub.mode"))
    return Response(status_code=403)


@router.post(WEBHOOK_PATH)
async def receive(request: Request, background_tasks: BackgroundTasks) -> Response:
    state = request.app.state
    settings = state.settings
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if settings.validate_meta_signature and not is_valid_meta_signature(
        settings.meta_app_secret.get_secret_value(), body, signature
    ):
        log.warning(
            "webhook.signature_invalid",
            channel="meta",
            has_signature=signature is not None,
            hint="META_APP_SECRET must be the App Secret of the app sending the webhook",
        )
        return Response(status_code=403)

    try:
        payload = json.loads(body)
    except ValueError:
        log.warning("webhook.invalid_json", channel="meta")
        return Response(status_code=400)

    events = parse_meta_webhook(payload)
    for status in events.statuses:
        log.info(
            "meta.status.updated",
            message_id=status.message_id,
            status=status.status,
            to=mask_number(status.recipient),
            error_code=status.error_code,
            error_title=status.error_title,
        )

    for inbound in events.messages:
        log.info(
            "webhook.received",
            channel="meta",
            message_sid=inbound.message_sid,
            num_media=len(inbound.media),
            has_body=bool(inbound.body),
        )
        user = state.whitelist.lookup(inbound.from_number)
        if user is None:
            # Never answer numbers outside the whitelist, not even with an error.
            log.info("webhook.not_whitelisted", user_masked=mask_number(inbound.from_number))
            continue
        if not state.deduper.first_time(inbound.message_sid):
            log.info("webhook.duplicate", message_sid=inbound.message_sid)
            continue
        background_tasks.add_task(state.runner.handle, inbound, user)

    return PlainTextResponse("ok")
