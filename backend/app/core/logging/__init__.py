import logging
import sys

import structlog


def configure_logging(level: str) -> None:
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.AsyncBoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


__all__ = ["configure_logging"]
