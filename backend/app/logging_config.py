from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sys
from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_context: ContextVar[str | None] = ContextVar("job_id", default=None)

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "severity": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in ("request_id", "job_id", "trace_id", "duration_seconds", "outcome"):
            value = getattr(record, field, None)
            if value is None and field == "request_id":
                value = request_id_context.get()
            if value is None and field == "job_id":
                value = job_id_context.get()
            if value is not None:
                payload[field] = value
        if record.exc_info:
            exc_type = record.exc_info[0]
            if exc_type:
                payload["exception.type"] = exc_type.__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[handler],
        force=True,
    )
