"""POST /debug/analyze: run the analysis graph on an uploaded image, without WhatsApp.

Only mounted when DEBUG=true.
"""

from typing import Annotated
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.agent.graph import run_analysis
from app.agent.llm import LLMRefusalError
from app.media import MediaRejected, validate_image


def _local_only(request: Request) -> None:
    """Hide the endpoint from requests coming through ngrok (it adds X-Forwarded-For)."""
    if "x-forwarded-for" in request.headers:
        raise HTTPException(404)


router = APIRouter(dependencies=[Depends(_local_only)])

_REJECT_STATUS = {"unsupported_type": 415, "too_large": 413}


@router.post("/debug/analyze")
async def debug_analyze(
    request: Request,
    image: Annotated[UploadFile | None, File()] = None,
    caption: Annotated[str | None, Form()] = None,
) -> dict:
    caption = (caption or "").strip() or None
    if image is None and caption is None:
        raise HTTPException(422, "Send an image and/or a caption")

    images = []
    if image is not None:
        try:
            images.append(validate_image(image.content_type or "", await image.read()))
        except MediaRejected as e:
            raise HTTPException(_REJECT_STATUS.get(e.reason, 400), e.reason) from e

    analysis_id = uuid4().hex[:8]
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    settings = request.app.state.settings
    with structlog.contextvars.bound_contextvars(analysis_id=analysis_id, source="debug"):
        try:
            final = await run_analysis(
                request.app.state.graph,
                {"analysis_id": analysis_id, "caption": caption, "images": images},
                config={
                    "run_name": "phishing-analysis",
                    "tags": ["debug"],
                    "metadata": {"analysis_id": analysis_id},
                    "configurable": {"log_content": settings.log_content},
                },
                on_event=on_event,
            )
        except LLMRefusalError as e:
            raise HTTPException(502, f"model refused (category={e.category})") from e

    return {
        "analysis_id": analysis_id,
        "extraction": final["extraction"].model_dump() if "extraction" in final else None,
        "verdict": final["verdict"].model_dump() if "verdict" in final else None,
        "reply_text": final.get("reply_text"),
        "events": events,
    }
