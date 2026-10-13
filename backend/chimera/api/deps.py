"""Request dependencies: the authenticated user and ownership helpers.

The security middleware has already rejected unauthenticated requests; these
dependencies re-check (defence in depth) and give routes the user object.
"""

from __future__ import annotations

from fastapi import Depends, Request

from ..errors import AppError
from ..state import AppState, get_state


def current_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise AppError(401, "auth_required", "Authentication required.")
    return user


def current_session(request: Request) -> dict:
    session = getattr(request.state, "session", None)
    if not session:
        raise AppError(401, "auth_required", "Authentication required.")
    return session


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        # 404 rather than 403: don't advertise admin endpoints.
        raise AppError(404, "not_found", "Not found")
    return user


def client_ip(request: Request) -> str:
    return getattr(request.state, "client_ip", None) or (request.client.host if request.client else "unknown")


__all__ = ["AppState", "get_state", "current_user", "current_session", "require_admin", "client_ip"]
