"""Structured logging configuration for one-0-one."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import litellm
import structlog

from src.settings import settings

_GAMEPLAY_LOGGERS = (
    "src.session",
    "src.games",
    "src.channels",
    "src.response_parser",
    "src.transcript",
)


class _LoggerPrefixFilter(logging.Filter):
    def __init__(self, prefixes: tuple[str, ...], *, include: bool) -> None:
        super().__init__()
        self._prefixes = prefixes
        self._include = include

    def filter(self, record: logging.LogRecord) -> bool:
        matches = any(
            record.name == prefix or record.name.startswith(f"{prefix}.")
            for prefix in self._prefixes
        )
        return matches if self._include else not matches


def _make_file_handler(
    path: Path,
    level: int,
    *,
    include_prefixes: tuple[str, ...] | None = None,
    exclude_prefixes: tuple[str, ...] | None = None,
) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    if include_prefixes is not None:
        handler.addFilter(_LoggerPrefixFilter(include_prefixes, include=True))
    if exclude_prefixes is not None:
        handler.addFilter(_LoggerPrefixFilter(exclude_prefixes, include=False))
    return handler


def _reset_root_logger(level: int) -> logging.Logger:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    return root


def _configure_noisy_library_loggers() -> None:
    litellm.set_verbose = False
    litellm.suppress_debug_info = True
    for name, level in {
        "LiteLLM": logging.WARNING,
        "litellm": logging.WARNING,
        "httpcore": logging.WARNING,
        "httpx": logging.WARNING,
        "uvicorn": logging.INFO,
        "asyncio": logging.WARNING,
    }.items():
        logging.getLogger(name).setLevel(level)


def _timestamped_log_path(directory: Path, stem: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return directory / f"{stem}_{timestamp}.log"


def configure_logging(level: str | None = None, *, mode: str = "default") -> None:
    """Configure structlog for the application."""
    resolved_level = level or settings.log_level
    log_level = getattr(logging, resolved_level.upper(), logging.INFO)
    root = _reset_root_logger(log_level)

    if mode == "tui":
        root.addHandler(
            _make_file_handler(
                _timestamped_log_path(settings.sessions_path, "gameplay"),
                log_level,
                include_prefixes=_GAMEPLAY_LOGGERS,
            )
        )
        root.addHandler(
            _make_file_handler(
                _timestamped_log_path(settings.logs_path, "application"),
                log_level,
                exclude_prefixes=_GAMEPLAY_LOGGERS,
            )
        )
    else:
        console = logging.StreamHandler()
        console.setLevel(log_level)
        console.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(console)

    _configure_noisy_library_loggers()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
