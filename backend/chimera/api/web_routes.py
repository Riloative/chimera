"""Web reading & search API.

There is deliberately no endpoint that fetches an arbitrary URL for the
browser or returns fetched bytes: pages are only read as part of a chat turn
(or re-read for a source the user already owns), and the browser only ever
receives extracted text and metadata.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import AppError
from ..web.errors import WebError
from ..web.search import EngineConfig
from ..web.service import source_meta
from ..web.settings import WebAdminSettings
from .deps import AppState, current_user, get_state, require_admin

router = APIRouter(prefix="/api")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineKeys(Strict):
    searxng: str | None = Field(None, max_length=4000)
    brave: str | None = Field(None, max_length=4000)
    tavily: str | None = Field(None, max_length=4000)


class AdminWebPatch(Strict):
    settings: dict | None = None
    # Write-only. A string sets the key, null removes it, absent leaves it.
    engine_keys: EngineKeys | None = None


class EngineTest(Strict):
    kind: Literal["searxng", "brave", "tavily"]
    searxng_url: str | None = Field(None, max_length=500)
    searxng_auth_header: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9-]*$")
    api_key: str | None = Field(None, max_length=4000)  # empty: use the stored key
    query: str = Field("open source software", min_length=1, max_length=200)


def _validation(exc: ValidationError) -> AppError:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []))
    return AppError(422, "validation_error", str(first.get("msg", "Invalid value")), field=field or None)


# ----------------------------------------------------------------- users
@router.get("/web/status")
async def web_status(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return state.web.status_for(user)


def _owned_source(state: AppState, user: dict, sid: str) -> dict:
    row = state.web.owned(user["id"], sid)
    if not row:
        raise WebError("web.source_not_found", "Source not found.")
    return row


@router.get("/web/sources/{sid}")
async def get_source(sid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    row = _owned_source(state, user, sid)
    return {**source_meta(row), "content": row["content"]}


@router.post("/web/sources/{sid}/refetch")
async def refetch_source(sid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    row = _owned_source(state, user, sid)
    settings = state.web.admin()
    if not settings.enabled:
        raise WebError("web.disabled", "Web access is turned off on this server.")
    if state.chat.active_for(user["id"], row["conversation_id"]):
        raise WebError("web.busy", "Wait until the current response has finished.")
    data = await state.web.read(user, row["url"], settings, robots=row["origin"] == "search" and settings.robots_for_search,
                                use_cache=False)
    if data.status != "ok":
        # Keep what was read before; report why the new attempt failed.
        return {"ok": False, "error_code": data.error_code, "error_params": data.error_params, "source": source_meta(row)}
    state.web.update_from(sid, data)
    return {"ok": True, "source": source_meta(state.web.owned(user["id"], sid))}


# ----------------------------------------------------------------- admin
@router.get("/admin/web")
async def admin_web(admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    return state.web.admin_view()


@router.patch("/admin/web")
async def admin_web_update(body: AdminWebPatch, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    if body.settings is not None:
        try:
            state.web.update_admin(body.settings)
        except ValidationError as exc:
            raise _validation(exc) from exc
    if body.engine_keys is not None:
        for kind, value in body.engine_keys.model_dump(exclude_unset=True).items():
            state.web.set_engine_key(kind, value)
    return state.web.admin_view()


@router.post("/admin/web/test")
async def admin_web_test(body: EngineTest, admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    settings = state.web.admin()
    stored = state.web.engine_config(body.kind, settings)
    url = settings.searxng_url if body.searxng_url is None else body.searxng_url.strip().rstrip("/")
    if body.kind == "searxng":
        try:
            WebAdminSettings.model_validate({**settings.model_dump(), "searxng_url": url})
        except ValidationError as exc:
            raise _validation(exc) from exc
    config = EngineConfig(
        kind=body.kind,
        base_url=url if body.kind == "searxng" else "",
        api_key=body.api_key or stored.api_key,
        auth_header=(body.searxng_auth_header if body.searxng_auth_header is not None else settings.searxng_auth_header) or None,
    )
    return await state.web.test(body.kind, settings, config, body.query)
