"""Structured JSON logging with redaction of sensitive keys."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

SENSITIVE_KEYS = {"password", "token", "authorization", "api_key", "secret", "cookie", "resume_text", "raw_text"}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return EMAIL_RE.sub("[email]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": EMAIL_RE.sub("[email]", record.getMessage()),
        }
        extra = getattr(record, "ctx", None)
        if extra:
            payload["ctx"] = redact(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_ctx(**kwargs: object) -> dict[str, object]:
    """Usage: logger.info("msg", extra=log_ctx(user_id=1))."""
    return {"ctx": kwargs}
