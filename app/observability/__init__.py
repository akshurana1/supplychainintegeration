"""Structured logging configuration using structlog.

Provides JSON-formatted logs with correlation IDs (run_id) so that
every log line from a single sync run can be traced end-to-end.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional
import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog and stdlib logging for the application.

    In production the output is pure JSON (one object per line) which is
    ingested natively by CloudWatch Logs / Datadog / Splunk.  Locally
    the renderer can be swapped to ``dev`` for coloured, human-friendly
    output.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib root logger so third-party libraries also emit
    # structured output.
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.handlers = [handler]


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger, optionally scoped to *name*."""
    return structlog.get_logger(name)
