"""Structured logging setup.

structlog sits on top of the stdlib `logging` module so that our own events and
third-party logs (uvicorn, httpx, ...) go through the same formatter.
Console output is human readable (`console`) or JSON (`json`); the optional log
file is always JSON lines.
"""

import logging
from pathlib import Path
from typing import Literal

import structlog

_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def _formatter(renderer) -> structlog.stdlib.ProcessorFormatter:
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )


def configure_logging(
    level: str, fmt: Literal["console", "json"], log_file: Path | None
) -> None:
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # No caching: keeps structlog.testing.capture_logs() working in tests.
        cache_logger_on_first_use=False,
    )

    console_renderer = (
        structlog.dev.ConsoleRenderer()
        if fmt == "console"
        else structlog.processors.JSONRenderer(ensure_ascii=False)
    )
    console = logging.StreamHandler()
    console.setFormatter(_formatter(console_renderer))
    handlers: list[logging.Handler] = [console]

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(_formatter(structlog.processors.JSONRenderer(ensure_ascii=False)))
        handlers.append(file_handler)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn logs through our handlers instead of its own.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    # At INFO httpx would log every Twilio media URL it fetches.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("twilio").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def mask_number(raw: str) -> str:
    """Mask a phone number for logs: '+393331234567' -> '+39***4567'."""
    number = raw.removeprefix("whatsapp:")
    if len(number) < 7:
        return "***"
    return f"{number[:3]}***{number[-4:]}"
