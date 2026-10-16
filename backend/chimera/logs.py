"""Logging setup: plain text (default) or one JSON object per line
(``CHIMERA_LOG_FORMAT=json``) for log collectors.

The rules are the same in both formats: no message content, passwords,
tokens, API keys or full web URLs are logged (callers log domains and IDs).
Access-log lines additionally drop query strings, which can carry search
terms typed by users.
"""

from __future__ import annotations

import json
import logging
import logging.config
import time
from typing import Any

# Only these record attributes reach a JSON line; anything a caller passes in
# ``extra=`` is ignored, so nothing unexpected can leak into structured logs.
JSON_FIELDS = ("time", "level", "logger", "message")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


class StripQueryFilter(logging.Filter):
    """Remove ``?query`` from uvicorn access-log request paths."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def logging_config(level: str = "info", fmt: str = "text") -> dict:
    formatter = (
        {"()": "chimera.logs.JsonFormatter"} if fmt == "json"
        else {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    )
    access = (
        {"()": "chimera.logs.JsonFormatter"} if fmt == "json"
        else {"format": '%(asctime)s %(levelname)s access %(message)s'}
    )
    lvl = level.upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"strip_query": {"()": "chimera.logs.StripQueryFilter"}},
        "formatters": {"default": formatter, "access": access},
        "handlers": {
            "default": {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"},
            "access": {"class": "logging.StreamHandler", "formatter": "access", "filters": ["strip_query"],
                       "stream": "ext://sys.stdout"},
        },
        "root": {"handlers": ["default"], "level": lvl},
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": lvl, "propagate": False},
            "uvicorn.error": {"level": lvl},
            "uvicorn.access": {"handlers": ["access"], "level": lvl, "propagate": False},
            # Outbound request lines can contain provider and page URLs.
            "httpx": {"level": "WARNING"},
            "httpcore": {"level": "WARNING"},
        },
    }


def configure_logging(level: str = "info", fmt: str = "text") -> dict:
    config = logging_config(level, fmt)
    logging.config.dictConfig(config)
    return config
