"""Web feature errors. Codes are stable (``web.*``) and translated by the UI."""

from __future__ import annotations

from typing import Any

from ..errors import AppError

# HTTP status used when a WebError escapes to an API response.
STATUS = {
    "web.disabled": 403,
    "web.search_unavailable": 409,
    "web.rate_limited": 429,
    "web.source_not_found": 404,
    "web.busy": 409,
}


class WebError(AppError):
    def __init__(self, code: str, message: str, status: int | None = None, **params: Any):
        super().__init__(status or STATUS.get(code, 400), code, message, **params)
