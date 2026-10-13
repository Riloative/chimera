"""Authentication, account self-service and admin user management."""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Depends, Request, Response

from ..errors import AppError
from ..security import CSRF_COOKIE, SESSION_COOKIE, cookie_secure, parse_cookies
from ..services.auth import user_out
from . import schemas
from .deps import AppState, client_ip, current_session, current_user, get_state, require_admin
from .routes import delete_owned_data

log = logging.getLogger("chimera.auth")
router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ cookies
def _set_cookies(response: Response, request: Request, state: AppState, token: str | None, csrf: str, remember: bool) -> None:
    secure = cookie_secure(state.config, request.scope)
    if token is not None:
        max_age = int(state.config.session_remember_days * 86400) if remember else None
        response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, samesite="lax", secure=secure, path="/")
    # Readable by our own JS (double-submit), useless to other origins.
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax", secure=secure, path="/")


def _clear_cookies(response: Response, request: Request, state: AppState) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    # A fresh anonymous CSRF token so the login form keeps working.
    _set_cookies(response, request, state, None, secrets.token_urlsafe(32), False)


def _start_session(request: Request, response: Response, state: AppState, user: dict, remember: bool) -> None:
    # Rotate: whatever session this browser had is ended first.
    old = parse_cookies(dict(request.scope.get("headers") or [])).get(SESSION_COOKIE)
    old_info = state.auth.resolve(old) if old else None
    if old_info:
        state.auth.revoke(old_info.user["id"], old_info.session["id"])
    token, csrf = state.auth.create_session(
        user["id"], remember=remember, user_agent=request.headers.get("user-agent"), ip=client_ip(request)
    )
    _set_cookies(response, request, state, token, csrf, remember)


def _me(state: AppState, user: dict) -> dict:
    out = user_out(user)
    out["legacy_notice"] = state.settings.private(user["id"], "legacy_notice")
    return out


# --------------------------------------------------------------------- auth
@router.get("/auth/status")
async def auth_status(request: Request, response: Response, state: AppState = Depends(get_state)):
    user = getattr(request.state, "user", None)
    cookies = parse_cookies(dict(request.scope.get("headers") or []))
    if not cookies.get(CSRF_COOKIE):
        _set_cookies(response, request, state, None, secrets.token_urlsafe(32), False)
    needs_setup = state.auth.user_count() == 0
    return {
        "authenticated": bool(user),
        "user": _me(state, user) if user else None,
        "registration_open": state.auth.registration_open(),
        "needs_setup": needs_setup,
        "setup_token_required": needs_setup and bool(state.config.setup_token),
    }


@router.post("/auth/register", status_code=201)
async def register(body: schemas.RegisterBody, request: Request, response: Response, state: AppState = Depends(get_state)):
    state.auth.check_register_rate(client_ip(request))
    first = state.auth.user_count() == 0
    if not first and state.auth.registration_mode() != "open":
        raise AppError(403, "registration_closed", "Registration is closed. Ask an administrator for an account.")
    if first and state.config.setup_token and not secrets.compare_digest(body.setup_token or "", state.config.setup_token):
        raise AppError(403, "setup_token_invalid", "The setup token is incorrect.", field="setup_token")
    if body.password != body.password_confirm:
        raise AppError(422, "password_mismatch", "The passwords don't match.", field="password_confirm")
    user, adopted = state.auth.create_user(
        body.email, body.username, body.password, language=body.language, enforce_first_user=True
    )
    if user["role"] == "admin" and state.auth.user_count() == 1:
        state.registry.seed_default(user["id"], state.config.seed_provider_url)
        if state.auth.registration_mode() == "first_user_only":
            log.info("first admin created; registration is now closed (mode first_user_only)")
    log.info("account created: %s (%s)", user["id"], user["role"])
    _start_session(request, response, state, user, body.remember)
    return _me(state, user)


@router.post("/auth/login")
async def login(body: schemas.LoginBody, request: Request, response: Response, state: AppState = Depends(get_state)):
    user = state.auth.login(body.identifier, body.password, client_ip(request))
    _start_session(request, response, state, user, body.remember)
    return _me(state, state.auth.get(user["id"]))  # type: ignore[arg-type]


@router.post("/auth/logout")
async def logout(request: Request, response: Response, state: AppState = Depends(get_state)):
    session = getattr(request.state, "session", None)
    if session:
        state.auth.revoke(session["user_id"], session["id"])
    _clear_cookies(response, request, state)
    return {"authenticated": False}


@router.get("/auth/me")
async def me(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return _me(state, user)


# ------------------------------------------------------------------ account
def _require_password(state: AppState, user: dict, password: str | None) -> None:
    if not password or not state.auth.verify_password(user, password):
        raise AppError(403, "current_password_incorrect", "Your current password is incorrect.", field="current_password")


@router.patch("/account")
async def update_account(body: schemas.ProfileUpdate, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    email_changed = body.email is not None and body.email.strip().lower() != user["email_norm"]
    name_changed = body.username is not None and body.username.strip() != user["username"]
    if email_changed or name_changed:
        _require_password(state, user, body.current_password)
    updated = state.auth.update_profile(
        user["id"],
        email=body.email if email_changed else None,
        username=body.username if name_changed else None,
        language=body.language,
    )
    return _me(state, updated)


@router.post("/account/password")
async def change_password(
    body: schemas.PasswordChange,
    request: Request,
    response: Response,
    user: dict = Depends(current_user),
    session: dict = Depends(current_session),
    state: AppState = Depends(get_state),
):
    _require_password(state, user, body.current_password)
    if body.new_password != body.new_password_confirm:
        raise AppError(422, "password_mismatch", "The passwords don't match.", field="new_password_confirm")
    if body.new_password == body.current_password:
        raise AppError(422, "password_unchanged", "Choose a password different from the current one.", field="new_password")
    # Revokes every other session; this one stays signed in.
    state.auth.set_password(user["id"], body.new_password, keep_session=session["id"])
    log.info("password changed for user %s", user["id"])
    return _me(state, state.auth.get(user["id"]))  # type: ignore[arg-type]


@router.get("/account/sessions")
async def list_sessions(user: dict = Depends(current_user), session: dict = Depends(current_session), state: AppState = Depends(get_state)):
    return [{**s, "remember": bool(s["remember"]), "current": s["id"] == session["id"]} for s in state.auth.sessions_for(user["id"])]


@router.delete("/account/sessions/{sid}", status_code=204)
async def revoke_session(sid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.auth.revoke(user["id"], sid):
        raise AppError(404, "session_not_found", "Session not found")
    return Response(status_code=204)


@router.post("/account/sessions/revoke-others")
async def revoke_other_sessions(user: dict = Depends(current_user), session: dict = Depends(current_session), state: AppState = Depends(get_state)):
    return {"revoked": state.auth.revoke_all(user["id"], except_session=session["id"])}


@router.post("/account/notice/dismiss", status_code=204)
async def dismiss_notice(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    state.settings.clear_private(user["id"], "legacy_notice")
    return Response(status_code=204)


def delete_user_completely(state: AppState, user_id: str) -> None:
    delete_owned_data(state, user_id, everything=True)
    state.files.delete_user_files(user_id)
    # Cascades: sessions, settings, remaining connections (incl. shared), etc.
    state.db.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _ensure_not_last_admin(state: AppState, target: dict) -> None:
    if target["role"] == "admin" and target["active"] and state.auth.admin_count() <= 1:
        raise AppError(409, "last_admin", "At least one active administrator is required.")


@router.delete("/account", status_code=204)
async def delete_account(
    body: schemas.AccountDelete, request: Request, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    _require_password(state, user, body.password)
    _ensure_not_last_admin(state, user)
    delete_user_completely(state, user["id"])
    log.info("account deleted: %s", user["id"])
    response = Response(status_code=204)
    _clear_cookies(response, request, state)
    return response


# -------------------------------------------------------------------- admin
def _target(state: AppState, uid: str) -> dict:
    row = state.auth.get(uid)
    if not row:
        raise AppError(404, "user_not_found", "User not found")
    return row


@router.get("/admin/users")
async def admin_list_users(admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    return [user_out(u) for u in state.db.all("SELECT * FROM users ORDER BY created_at")]


@router.post("/admin/users", status_code=201)
async def admin_create_user(body: schemas.AdminUserCreate, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    temporary = body.password is None
    password = body.password or state.auth.temporary_password()
    user, _ = state.auth.create_user(body.email, body.username, password, role=body.role, must_change_password=temporary)
    log.info("admin %s created user %s", admin["id"], user["id"])
    return {**user_out(user), "temporary_password": password if temporary else None}


@router.patch("/admin/users/{uid}")
async def admin_update_user(uid: str, body: schemas.AdminUserUpdate, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    target = _target(state, uid)
    demoting = body.role == "user" and target["role"] == "admin"
    disabling = body.active is False and target["active"]
    if demoting or disabling:
        _ensure_not_last_admin(state, target)
    sets, values = [], []
    if body.role is not None:
        sets.append("role = ?")
        values.append(body.role)
    if body.active is not None:
        sets.append("active = ?")
        values.append(int(body.active))
    if sets:
        state.db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*values, uid))
    if disabling:
        state.auth.revoke_all(uid)
    return user_out(_target(state, uid))


@router.post("/admin/users/{uid}/reset-password")
async def admin_reset_password(uid: str, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    _target(state, uid)
    temporary = state.auth.temporary_password()
    state.auth.set_password(uid, temporary, must_change=True)
    log.info("admin %s reset the password of user %s", admin["id"], uid)
    return {"temporary_password": temporary}


@router.delete("/admin/users/{uid}", status_code=204)
async def admin_delete_user(uid: str, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    target = _target(state, uid)
    if uid == admin["id"]:
        raise AppError(409, "cannot_delete_self", "Use Account settings to delete your own account.")
    _ensure_not_last_admin(state, target)
    delete_user_completely(state, uid)
    log.info("admin %s deleted user %s", admin["id"], uid)
    return Response(status_code=204)


@router.get("/admin/settings")
async def admin_get_settings(admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    return {
        "registration_mode": state.auth.registration_mode(),
        "provider_url_policy": state.auth.app_setting("provider_url_policy", "private"),
        "provider_url_allowlist": state.auth.app_setting("provider_url_allowlist", []),
    }


@router.patch("/admin/settings")
async def admin_patch_settings(body: schemas.AdminSettings, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    for key, value in body.model_dump(exclude_none=True).items():
        if key == "provider_url_allowlist":
            value = [v.strip() for v in value if v.strip()]
        state.auth.set_app_setting(key, value)
        log.info("admin %s set %s", admin["id"], key)
    return await admin_get_settings(admin, state)


