"""FastAPI application factory.

Run with: uv run uvicorn app.main:create_app --factory --port 8000
"""

import asyncio
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request

from app.agent.graph import build_graph
from app.agent.llm import AnalysisLLM, create_llm
from app.agent.runner import AnalysisRunner, MessageDeduper
from app.api import debug, health, meta_webhook, twilio_webhook
from app.api.inbound import accept_inbound
from app.channels.meta_client import MetaGateway
from app.channels.models import MessagingGateway
from app.channels.twilio_client import TwilioGateway
from app.channels.twilio_poller import TwilioPoller
from app.config import Settings
from app.logging import configure_logging, get_logger
from app.whitelist import Whitelist

log = get_logger(__name__)


def create_gateway(settings: Settings) -> MessagingGateway:
    """The outbound side of the channel selected with CHANNEL."""
    return MetaGateway(settings) if settings.channel == "meta" else TwilioGateway(settings)


def _polling_lifespan(settings: Settings):
    """Start the Twilio poller with the app and stop it on shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = app.state
        tasks: set[asyncio.Task] = set()

        async def on_message(inbound) -> None:
            user = accept_inbound(state, inbound, source="twilio_polling")
            if user is not None:
                task = asyncio.create_task(state.runner.handle(inbound, user))
                tasks.add(task)
                task.add_done_callback(tasks.discard)

        gateway = state.gateway
        client = (gateway if isinstance(gateway, TwilioGateway) else TwilioGateway(settings))
        poller = TwilioPoller(settings, client=client.rest_client(), on_message=on_message)
        stop = asyncio.Event()
        poll_task = asyncio.create_task(poller.run(stop))
        try:
            yield
        finally:
            stop.set()
            await poll_task

    return lifespan


def create_app(
    settings: Settings | None = None,
    *,
    llm: AnalysisLLM | None = None,
    gateway: MessagingGateway | None = None,
    whitelist: Whitelist | None = None,
) -> FastAPI:
    if settings is None:
        # Exports .env into os.environ so LangSmith (LANGSMITH_*) can read it too.
        load_dotenv()
        settings = Settings()
    settings.check_channel_credentials()
    configure_logging(settings.log_level, settings.log_format, settings.log_file)

    polling = settings.inbound_mode == "polling"
    app = FastAPI(
        title="WhatsApp Phishing Agent",
        docs_url="/docs" if settings.debug else None,
        lifespan=_polling_lifespan(settings) if polling else None,
    )
    app.state.settings = settings
    app.state.graph = build_graph(llm or create_llm(settings))
    # Fails closed: an invalid or missing whitelist stops the app from starting.
    app.state.whitelist = whitelist if whitelist is not None else Whitelist(settings.whitelist_path)
    app.state.deduper = MessageDeduper()
    app.state.gateway = gateway or create_gateway(settings)
    app.state.runner = AnalysisRunner(app.state.graph, app.state.gateway, settings)

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
    # Only the active channel's webhook is mounted, and none when polling Twilio.
    if settings.channel == "meta":
        app.include_router(meta_webhook.router)
    elif not polling:
        app.include_router(twilio_webhook.router)
    if settings.debug:
        app.include_router(debug.router)

    log.info(
        "app.started",
        channel=settings.channel,
        inbound_mode=settings.inbound_mode,
        model=settings.anthropic_model,
        debug=settings.debug,
    )
    return app
