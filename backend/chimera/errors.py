"""Machine-readable API errors.

Every error response has the shape::

    {"detail": {"code": "conversation_not_found", "message": "...", "params": {...}}}

``code`` is stable and is what the web UI translates; ``message`` is an English
fallback for API clients and logs; ``params`` feed interpolation placeholders.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, **params: Any):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.params = {k: v for k, v in params.items() if v is not None}

    def body(self) -> dict[str, Any]:
        return {"detail": {"code": self.code, "message": self.message, "params": self.params}}

    def response(self, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status, headers=headers)


def not_found(code: str = "not_found", message: str = "Not found") -> AppError:
    return AppError(404, code, message)


def bad_request(code: str, message: str, **params: Any) -> AppError:
    return AppError(400, code, message, **params)


def forbidden(code: str, message: str, **params: Any) -> AppError:
    return AppError(403, code, message, **params)
