from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "model": request.app.state.settings.anthropic_model}
