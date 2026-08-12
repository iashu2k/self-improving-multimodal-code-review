import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
  logging.basicConfig(
    format="%(message)s",
    stream=sys.stdout,
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
  )

  structlog.configure(
    processors=[
      structlog.contextvars.merge_contextvars,
      structlog.processors.add_log_level,
      structlog.processors.TimeStamper(fmt="iso", utc=True),
      structlog.processors.StackInfoRenderer(),
      structlog.processors.format_exc_info,
      structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
  )
