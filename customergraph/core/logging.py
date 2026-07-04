"""Logging helpers for CustomerGraph AI.

Day 2 adds a consistent logger and request logging middleware so every future
API, auth action, graph call, and agent call can be traced cleanly.
"""

from __future__ import annotations

import logging
import sys
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def configure_logging(log_level: str = "INFO") -> None:
    """Configure application-wide logging once."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # Keep noisy server logs readable while preserving useful access logs.
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger."""
    return logging.getLogger(name)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next) -> Response:
        logger = get_logger("customergraph.request")
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                "request_failed request_id=%s method=%s path=%s duration_ms=%s",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
            )
            raise

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-MS"] = str(duration_ms)

        logger.info(
            "request_completed request_id=%s actor_user_id=%s actor_role=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            getattr(request.state, "current_user_id", None),
            getattr(request.state, "current_user_role", None),
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response
