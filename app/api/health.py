from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    return {
        "status": "ok",
        "model": state.settings.anthropic_model,
        "whitelist_entries": len(state.whitelist),
    }
