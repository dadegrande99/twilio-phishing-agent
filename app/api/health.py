from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    return {
        "status": "ok",
        "channel": state.settings.channel,
        "model": state.settings.anthropic_model,
        "whitelist_entries": len(state.whitelist),
    }
