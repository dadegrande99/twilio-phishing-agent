"""What every inbound message goes through, whatever the channel or delivery mode:
log it, drop numbers outside the whitelist, drop duplicates."""

from typing import Literal

from app.channels.models import InboundMessage
from app.logging import get_logger, mask_number
from app.whitelist import AllowedUser

log = get_logger(__name__)

InboundSource = Literal["twilio_webhook", "meta_webhook", "twilio_polling"]


def accept_inbound(state, inbound: InboundMessage, *, source: InboundSource) -> AllowedUser | None:
    """Return the whitelisted user if the message must be analysed, else None."""
    log.info(
        "inbound.received",
        source=source,
        message_sid=inbound.message_sid,
        num_media=len(inbound.media),
        has_body=bool(inbound.body),
    )
    user = state.whitelist.lookup(inbound.from_number)
    if user is None:
        # Never answer numbers outside the whitelist, not even with an error.
        log.info("inbound.not_whitelisted", source=source,
                 user_masked=mask_number(inbound.from_number))
        return None
    if not state.deduper.first_time(inbound.message_sid):
        log.info("inbound.duplicate", source=source, message_sid=inbound.message_sid)
        return None
    return user
