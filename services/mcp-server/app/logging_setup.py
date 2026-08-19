"""Structured JSON logging. Local copy of the integration-api module:
services never share code across container boundaries."""
import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            entry.update(extra)
        return json.dumps(entry, default=str)


def setup_json_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
