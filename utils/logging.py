import logging
import os
import re
import contextvars
from typing import Any, Dict

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - fallback when structlog is missing
    structlog = None  # type: ignore[assignment]

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_EMAIL_RE = re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b")
_DIGIT_RE = re.compile(r"\d")

# Maximum number of characters from HTTP response bodies to keep in logs
MAX_BODY_LOG = 500


def truncate_body(body: str | None, limit: int = MAX_BODY_LOG) -> str:
    """Return body truncated to *limit* characters for safe logging."""
    if body is None:
        return ""
    if len(body) <= limit:
        return body
    return body[:limit] + "...[truncated]"

def _mask_value(value: str) -> str:
    value = _EMAIL_RE.sub("[EMAIL]", value)
    value = _DIGIT_RE.sub("X", value)
    return value

def mask_sensitive_data(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    return {k: _mask_value(v) if isinstance(v, str) else v for k, v in event_dict.items()}

def add_request_id(
    logger: logging.Logger, method_name: str, event_dict: Dict[str, Any]
) -> Dict[str, Any]:
    request_id = _request_id.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict

def set_request_id(request_id: str | None) -> None:
    _request_id.set(request_id)

def configure_logging() -> None:
    logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO), format="%(message)s")
    if structlog:
        structlog.configure(
            processors=[
                add_request_id,
                mask_sensitive_data,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(),
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

def get_logger(name: str | None = None) -> Any:
    if structlog:
        return structlog.get_logger(name)
    return logging.getLogger(name)

configure_logging()
