"""Structured JSON logging (Section 44) with mandatory secret redaction
(Section 6, 40, 82). API keys, Authorization headers, and cookies must
never reach a log line."""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from typing import Any

import orjson

_REDACTED_KEYS = {"authorization", "cookie", "api_key", "api-key", "x-api-key", "password"}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _REDACTED_KEYS else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(_redact(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return orjson.dumps(payload).decode()


class _ExtraAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):  # type: ignore[override]
        extra = kwargs.pop("extra", None)
        if extra is not None:
            kwargs["extra"] = {"extra_fields": extra}
        return msg, kwargs


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def get_logger(name: str) -> _ExtraAdapter:
    return _ExtraAdapter(logging.getLogger(name), {})
