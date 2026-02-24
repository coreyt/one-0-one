"""
Structured logging configuration for one-0-one.

Usage:
    from src.logging import get_logger
    log = get_logger(__name__)
    log.info("session.started", session_id="abc", template="20-questions")

Context vars (session_id, template_title) are bound at session start and
propagate automatically to all log calls within that async context:
    import structlog
    structlog.contextvars.bind_contextvars(session_id="abc", title="My Session")
"""

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog for the application. Call once at startup."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # stdlib logging baseline (captures third-party logs too)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog bound logger for the given module name."""
    return structlog.get_logger(name)
