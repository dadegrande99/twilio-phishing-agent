"""FastAPI application factory.

Run with: uv run uvicorn app.main:create_app --factory --port 8000
"""

import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request

from app.agent.graph import build_graph
from app.agent.llm import AnalysisLLM, create_llm
from app.api import debug, health
from app.config import Settings
from app.logging import configure_logging, get_logger

log = get_logger(__name__)


def create_app(settings: Settings | None = None, *, llm: AnalysisLLM | None = None) -> FastAPI:
    if settings is None:
        # Exports .env into os.environ so LangSmith (LANGSMITH_*) can read it too.
        load_dotenv()
        settings = Settings()
    configure_logging(settings.log_level, settings.log_format, settings.log_file)

    app = FastAPI(title="WhatsApp Phishing Agent", docs_url="/docs" if settings.debug else None)
    app.state.settings = settings
    app.state.graph = build_graph(llm or create_llm(settings))

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000),
        )
        return response

    app.include_router(health.router)
    if settings.debug:
        app.include_router(debug.router)

    log.info("app.started", model=settings.anthropic_model, debug=settings.debug)
    return app
