"""Core HTTP API (chat, connections, files, prompts, knowledge, data).

Every route takes ``user = Depends(current_user)`` and every query is scoped
by ``user["id"]``. Records belonging to someone else are reported as missing
(404), never as forbidden, so their existence isn't revealed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .. import __version__
from ..db import dumps, is_chimera_database, new_id, now_iso
from ..errors import AppError
from ..netpolicy import url_allowed
from ..services.conversations import conversation_out, message_out
from ..services.documents import ExtractionError, extract, safe_filename
from ..services import tree as message_tree
from ..services.files import file_out
from ..web.service import source_meta
from . import schemas
from .deps import AppState, current_user, get_state, require_admin

router = APIRouter(prefix="/api")
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def missing(code: str, message: str) -> AppError:
    return AppError(404, code, message)


# --------------------------------------------------------------------- system
@router.get("/health")
async def health():
    # Public: intentionally reveals nothing beyond liveness.
    return {"status": "ok"}


@router.get("/info")
async def info(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    out = {"version": __version__, "telemetry": False, "registration_mode": state.auth.registration_mode()}
    if user["role"] == "admin":
        cfg = state.config
        out.update(
            data_dir=str(cfg.data_dir),
            database=str(cfg.db_path),
            db_size=cfg.db_path.stat().st_size if cfg.db_path.exists() else 0,
            users=state.auth.user_count(),
        )
    return out


# ------------------------------------------------------------------ settings
@router.get("/settings")
async def get_settings(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return state.settings.get(user["id"]).model_dump()


@router.patch("/settings")
async def patch_settings(request: Request, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    try:
        patch = await request.json()
    except ValueError as exc:
        raise AppError(400, "invalid_json", "Invalid JSON") from exc
    if not isinstance(patch, dict):
        raise AppError(400, "invalid_json", "Expected an object")
    try:
        return state.settings.update(user["id"], patch).model_dump()
    except Exception as exc:  # pydantic ValidationError
        errors = getattr(exc, "errors", None)
        first = errors()[0] if callable(errors) and errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []))
        raise AppError(422, "validation_error", str(first.get("msg", exc)), field=field or None) from exc


# ----------------------------------------------------------------- providers
def _url_policy(state: AppState) -> tuple[str, list[str]]:
    return state.auth.app_setting("provider_url_policy", "private"), state.auth.app_setting("provider_url_allowlist", [])


def _check_url(state: AppState, user: dict, url: str | None) -> None:
    if not url or user["role"] == "admin":
        return
    policy, allowlist = _url_policy(state)
    if not url_allowed(url, policy, allowlist):
        raise AppError(403, "provider_url_forbidden", "Your administrator does not allow connections to this address.")


def _editable_provider(state: AppState, user: dict, provider_id: str) -> dict:
    row = state.registry.visible(user, provider_id)
    if not row:
        raise missing("provider_not_found", "Connection not found")
    if not state.registry.can_edit(user, row):
        raise AppError(403, "provider_read_only", "Only an administrator can change a shared connection.")
    return row


@router.get("/providers")
async def list_providers(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return [state.registry.public(r, user) for r in state.registry.rows(user)]


@router.post("/providers", status_code=201)
async def create_provider(body: schemas.ProviderCreate, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _check_url(state, user, body.base_url)
    if body.shared and user["role"] != "admin":
        raise AppError(403, "provider_share_admin_only", "Only administrators can share connections.")
    data = body.model_dump(exclude={"shared"})
    row = state.registry.create(user["id"], data, shared=bool(body.shared))
    return state.registry.public(row, user)


@router.patch("/providers/{provider_id}")
async def update_provider(
    provider_id: str, body: schemas.ProviderUpdate, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    _editable_provider(state, user, provider_id)
    data = body.model_dump(exclude_unset=True)
    if "shared" in data and user["role"] != "admin":
        raise AppError(403, "provider_share_admin_only", "Only administrators can share connections.")
    _check_url(state, user, data.get("base_url"))
    row = state.registry.update(provider_id, data)
    return state.registry.public(row, user)  # type: ignore[arg-type]


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _editable_provider(state, user, provider_id)
    state.registry.delete(provider_id)
    settings = state.settings.get(user["id"])
    if settings.default_provider_id == provider_id:
        state.settings.update(user["id"], {"default_provider_id": None, "default_model": None})
    return Response(status_code=204)


@router.post("/providers/test")
async def test_unsaved_provider(body: schemas.ProviderTest, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _check_url(state, user, body.base_url)
    data = body.model_dump()
    if body.provider_id and body.api_key is None:
        stored = state.registry.visible(user, body.provider_id)
        if stored and state.registry.can_edit(user, stored):
            data["api_key"] = state.registry.box.decrypt(stored.get("api_key"))
    return await state.registry.test_unsaved(data)


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.registry.visible(user, provider_id):
        raise missing("provider_not_found", "Connection not found")
    result = await state.registry.test(provider_id)
    return {**result, "provider": state.registry.public(state.registry.row(provider_id), user)}  # type: ignore[arg-type]


@router.get("/models")
async def list_models(refresh: bool = False, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return {"providers": await state.registry.discover_all(user, refresh)}


# ------------------------------------------------------------- conversations
def _owned_conversation(state: AppState, user: dict, cid: str) -> dict:
    row = state.conversations.owned(user["id"], cid)
    if not row:
        raise missing("conversation_not_found", "Conversation not found")
    return row


@router.get("/conversations")
async def list_conversations(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    items = state.conversations.list(user["id"])
    for item in items:
        item["generating"] = state.chat.active_for(user["id"], item["id"]) is not None
    return items


@router.get("/conversations/search")
async def search_conversations(
    q: str = Query("", max_length=500), user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    return state.conversations.search(user["id"], q)


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: schemas.ConversationCreate, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    data = body.model_dump()
    if body.params is not None:
        data["params"] = body.params.model_dump(exclude_none=True)
    data["collection_ids"] = state.knowledge.owned_ids(user["id"], body.collection_ids)
    if body.provider_id and not state.registry.visible(user, body.provider_id):
        data["provider_id"] = data["model"] = None
    return conversation_out(state.conversations.create(user["id"], data))


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    row = _owned_conversation(state, user, cid)
    gen = state.chat.active_for(user["id"], cid)
    out = conversation_out(row)
    out["messages"] = state.conversations.messages(cid)
    out["active_path"] = message_tree.active_path(state.db, cid)
    out["web_sources"] = state.web.for_conversation(user["id"], cid)
    out["generation_id"] = gen.id if gen else None
    if gen:  # expose the freshest partial text
        for m in out["messages"]:
            if m["id"] == gen.message_id:
                m["content"], m["reasoning"] = gen.content, gen.reasoning
    return out


@router.patch("/conversations/{cid}")
async def update_conversation(
    cid: str, body: schemas.ConversationUpdate, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    _owned_conversation(state, user, cid)
    data = body.model_dump(exclude_unset=True)
    if "params" in data:
        data["params"] = body.params.model_dump(exclude_none=True) if body.params else {}
    if "title" in data:
        data["title"] = data["title"].strip() or "Untitled"
    if "collection_ids" in data:
        data["collection_ids"] = state.knowledge.owned_ids(user["id"], data["collection_ids"] or [])
    if data.get("provider_id") and not state.registry.visible(user, data["provider_id"]):
        raise missing("provider_not_found", "Connection not found")
    return conversation_out(state.conversations.update(cid, data))  # type: ignore[arg-type]


@router.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _owned_conversation(state, user, cid)
    gen = state.chat.active_for(user["id"], cid)
    if gen:
        state.chat.cancel(user["id"], gen.id)
    state.conversations.delete(user["id"], cid)
    return Response(status_code=204)


def _stream(state: AppState, gen, initial) -> StreamingResponse:
    stream = state.chat.subscribe(gen, initial)  # attach before launching
    state.chat.launch(gen)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _start_payload(state: AppState, gen, extra: dict) -> list:
    row = state.conversations.message(gen.message_id)
    path = message_tree.active_path(state.db, gen.conversation_id)
    return [("start", {"generation_id": gen.id, "conversation_id": gen.conversation_id,
                       "assistant_message": message_out(row), "active_path": path, **extra})]  # type: ignore[arg-type]


@router.post("/conversations/{cid}/messages")
async def send_message(
    cid: str, body: schemas.SendMessage, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    gen, extra = state.chat.prepare_send(user, cid, body.content, body.attachments, body.provider_id, body.model, params,
                                         body.urls, body.web_search, body.timezone)
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.post("/conversations/{cid}/regenerate")
async def regenerate(cid: str, body: schemas.Regenerate, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    gen, extra = state.chat.prepare_regenerate(user, cid, body.message_id, body.provider_id, body.model, params,
                                               body.web_search, body.timezone)
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.post("/conversations/{cid}/messages/{mid}/edit")
async def edit_message(
    cid: str, mid: str, body: schemas.EditMessage, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    gen, extra = state.chat.prepare_edit(user, cid, mid, body.content, body.provider_id, body.model, params,
                                         body.urls, body.web_search, body.timezone)
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.get("/conversations/{cid}/stream")
async def reattach(cid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _owned_conversation(state, user, cid)
    gen = state.chat.active_for(user["id"], cid)
    if not gen:
        return Response(status_code=204)
    stream = state.chat.subscribe(gen, [state.chat.snapshot_event(gen)])
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/generations/{gid}/cancel")
async def cancel_generation(gid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    gen = state.chat.active.get(gid)
    if not gen or gen.user["id"] != user["id"]:
        raise missing("generation_not_found", "Generation not found")
    return {"cancelled": state.chat.cancel(user["id"], gid)}


@router.post("/conversations/{cid}/active")
async def set_active_branch(
    cid: str, body: schemas.ActiveBranch, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    """Show the branch through ``message_id`` (its latest continuation)."""
    _owned_conversation(state, user, cid)
    tree = message_tree.load(state.db, cid)
    if body.message_id not in tree.nodes:
        raise missing("branch_not_found", "Message not found in this conversation")
    path = message_tree.activate(state.db, cid, body.message_id, tree=tree)
    return {"active_leaf_id": path[-1], "active_path": path}


@router.delete("/messages/{mid}")
async def delete_message(mid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    """Delete a message and everything that branches from it."""
    row = state.conversations.owned_message(user["id"], mid)
    if not row:
        raise missing("message_not_found", "Message not found")
    tree = message_tree.load(state.db, row["conversation_id"])
    if any(tree.nodes[n]["status"] == "streaming" for n in tree.subtree(mid)):
        raise AppError(409, "message_streaming", "Stop the response before deleting it.")
    deleted, path = message_tree.delete_subtree(state.db, row["conversation_id"], mid)
    return {"deleted": deleted, "active_path": path}


@router.patch("/messages/{mid}")
async def update_message(mid: str, body: schemas.MessageUpdate, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    """Pin a message to the model's context (always included when it fits)."""
    row = state.conversations.owned_message(user["id"], mid)
    if not row:
        raise missing("message_not_found", "Message not found")
    state.db.execute("UPDATE messages SET pinned = ? WHERE id = ?", (1 if body.pinned else 0, mid))
    return message_out(state.conversations.message(mid))  # type: ignore[arg-type]


# --------------------------------------------------------------------- files
async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "file_too_large", f"File exceeds the {limit // (1024 * 1024)} MB limit.", mb=limit // (1024 * 1024))
    return data


def _extraction_error(exc: ExtractionError) -> AppError:
    return AppError(422, exc.code, str(exc), **exc.params)


@router.post("/files", status_code=201)
async def upload_file(file: UploadFile = File(...), user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    settings = state.settings.get(user["id"])
    limit = settings.max_attachment_mb * 1024 * 1024
    data = await _read_limited(file, limit)
    filename = safe_filename(file.filename or "file")
    try:
        extracted = extract(filename, data, limit, settings.max_attachment_chars)
    except ExtractionError as exc:
        raise _extraction_error(exc) from exc
    row = state.files.save(user["id"], filename, len(data), extracted, data)
    return file_out(row, include_text=False)


@router.get("/files/{fid}")
async def get_file(fid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    row = state.files.owned(user["id"], fid)
    if not row:
        raise missing("file_not_found", "File not found")
    return file_out(row, include_text=True)


@router.get("/files/{fid}/content")
async def get_file_content(fid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    row = state.files.owned(user["id"], fid)
    path = state.files.path(row) if row and row["kind"] == "image" else None
    if not path:
        raise missing("file_not_found", "File not found")
    return FileResponse(path, media_type=row["mime"], headers={"Content-Disposition": "inline"})  # type: ignore[index]


@router.delete("/files/{fid}", status_code=204)
async def delete_file(fid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.files.delete(user["id"], fid):
        raise missing("file_not_found", "File not found")
    return Response(status_code=204)


# ------------------------------------------------------------------- prompts
def _prompt_out(row: dict) -> dict:
    return {k: row[k] for k in ("id", "title", "description", "category", "content", "created_at", "updated_at")}


def _owned_prompt(state: AppState, user: dict, pid: str) -> dict:
    row = state.db.one("SELECT * FROM prompts WHERE id = ? AND user_id = ?", (pid, user["id"]))
    if not row:
        raise missing("prompt_not_found", "Prompt not found")
    return row


@router.get("/prompts")
async def list_prompts(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    rows = state.db.all("SELECT * FROM prompts WHERE user_id = ? ORDER BY category, title COLLATE NOCASE", (user["id"],))
    return [_prompt_out(r) for r in rows]


@router.post("/prompts", status_code=201)
async def create_prompt(body: schemas.PromptBody, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    pid, ts = new_id(), now_iso()
    state.db.execute(
        "INSERT INTO prompts(id, user_id, title, description, category, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (pid, user["id"], body.title.strip(), body.description.strip(), body.category.strip(), body.content, ts, ts),
    )
    return _prompt_out(_owned_prompt(state, user, pid))


@router.put("/prompts/{pid}")
async def update_prompt(pid: str, body: schemas.PromptBody, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _owned_prompt(state, user, pid)
    state.db.execute(
        "UPDATE prompts SET title=?, description=?, category=?, content=?, updated_at=? WHERE id=? AND user_id=?",
        (body.title.strip(), body.description.strip(), body.category.strip(), body.content, now_iso(), pid, user["id"]),
    )
    return _prompt_out(_owned_prompt(state, user, pid))


@router.delete("/prompts/{pid}", status_code=204)
async def delete_prompt(pid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    _owned_prompt(state, user, pid)
    state.db.execute("DELETE FROM prompts WHERE id = ? AND user_id = ?", (pid, user["id"]))
    return Response(status_code=204)


# ----------------------------------------------------------------- knowledge
def _owned_collection(state: AppState, user: dict, cid: str) -> dict:
    col = state.knowledge.collection(user["id"], cid)
    if not col:
        raise missing("collection_not_found", "Collection not found")
    return col


def _doc_out(row: dict) -> dict:
    return {k: v for k, v in row.items()}


@router.get("/collections")
async def list_collections(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return state.knowledge.collections(user["id"])


@router.post("/collections", status_code=201)
async def create_collection(body: schemas.CollectionBody, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    return state.knowledge.create_collection(user["id"], body.name.strip(), body.description.strip())


@router.get("/collections/{cid}")
async def get_collection(cid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    col = _owned_collection(state, user, cid)
    return {**col, "documents": [_doc_out(d) for d in state.knowledge.documents(cid)]}


@router.patch("/collections/{cid}")
async def update_collection(
    cid: str, body: schemas.CollectionUpdate, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    col = state.knowledge.update_collection(user["id"], cid, body.model_dump(exclude_unset=True))
    if not col:
        raise missing("collection_not_found", "Collection not found")
    return col


@router.delete("/collections/{cid}", status_code=204)
async def delete_collection(cid: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.knowledge.delete_collection(user["id"], cid):
        raise missing("collection_not_found", "Collection not found")
    return Response(status_code=204)


@router.post("/collections/{cid}/documents", status_code=201)
async def add_document(
    cid: str, file: UploadFile = File(...), user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    _owned_collection(state, user, cid)
    settings = state.settings.get(user["id"])
    limit = max(settings.max_attachment_mb, 50) * 1024 * 1024
    data = await _read_limited(file, limit)
    filename = safe_filename(file.filename or "document")
    try:
        extracted = extract(filename, data, limit, 20_000_000)
    except ExtractionError as exc:
        raise _extraction_error(exc) from exc
    if extracted.kind != "text":
        raise AppError(422, "collection_text_only", "Only text documents and PDFs can be added to a collection.")
    doc = await state.knowledge.add_document(user, cid, filename, extracted.mime, len(data), extracted.text or "", settings.knowledge)
    return _doc_out(doc)


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.knowledge.document(user["id"], doc_id):
        raise missing("document_not_found", "Document not found")
    await state.knowledge.embed_document(user, doc_id, state.settings.get(user["id"]).knowledge)
    return _doc_out(state.knowledge.document(user["id"], doc_id))  # type: ignore[arg-type]


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    if not state.knowledge.delete_document(user["id"], doc_id):
        raise missing("document_not_found", "Document not found")
    return Response(status_code=204)


@router.post("/collections/{cid}/search")
async def search_collection(
    cid: str, body: schemas.SearchBody, user: dict = Depends(current_user), state: AppState = Depends(get_state)
):
    _owned_collection(state, user, cid)
    hits = await state.knowledge.search(user, [cid], body.query, state.settings.get(user["id"]).knowledge, body.top_k)
    return [h.to_dict() for h in hits]


# ---------------------------------------------------------------------- data
EXPORT_VERSION = 3  # 3: messages carry parent_id; conversations carry active_leaf_id


def export_payload(state: AppState, user: dict) -> dict:
    conversations = []
    for row in state.db.all("SELECT * FROM conversations WHERE user_id = ? ORDER BY created_at", (user["id"],)):
        conv = conversation_out(row)
        conv["messages"] = state.conversations.messages(row["id"])
        # Pages read and search results, including the extracted text.
        conv["web_sources"] = [
            {**source_meta(r), "content": r["content"]} for r in state.web.full_for_conversation(user["id"], row["id"])
        ]
        conversations.append(conv)
    return {
        "format": "chimera-export",
        "version": EXPORT_VERSION,
        "exported_at": now_iso(),
        "account": {"username": user["username"], "email": user["email"], "language": user["language"]},
        "conversations": conversations,
        "prompts": [_prompt_out(r) for r in state.db.all("SELECT * FROM prompts WHERE user_id = ?", (user["id"],))],
        "settings": state.settings.get(user["id"]).model_dump(),
        # API keys are deliberately excluded.
        "providers": [
            {k: v for k, v in state.registry.public(r, user).items()
             if k in ("id", "name", "kind", "base_url", "api_path", "timeout", "enabled", "vision", "manual_models")}
            for r in state.db.all("SELECT * FROM providers WHERE user_id = ?", (user["id"],))
        ],
    }


@router.get("/data/export")
async def export_data(user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    filename = f"chimera-export-{user['username']}-{now_iso()[:10]}.json"
    return Response(
        content=json.dumps(export_payload(state, user), ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/data/import")
async def import_data(file: UploadFile = File(...), user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    raw = await _read_limited(file, 500 * 1024 * 1024)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise AppError(422, "import_invalid", "Not a valid JSON export.") from exc
    if not isinstance(payload, dict) or payload.get("format") != "chimera-export":
        raise AppError(422, "import_invalid", "This file is not a Chimera export.")
    uid = user["id"]
    imported = {"conversations": 0, "messages": 0, "prompts": 0, "skipped": 0}
    with state.db.transaction() as conn:
        for conv in payload.get("conversations") or []:
            if not isinstance(conv, dict) or not conv.get("id"):
                continue
            existing = conn.execute("SELECT user_id FROM conversations WHERE id = ?", (str(conv["id"]),)).fetchone()
            if existing and existing[0] == uid:
                imported["skipped"] += 1
                continue
            # An ID taken by someone else gets a fresh one (never touch their row).
            cid = new_id() if existing else str(conv["id"])
            ts = now_iso()
            conn.execute(
                """INSERT INTO conversations(id, user_id, title, title_source, provider_id, model, system_prompt, params,
                       collection_ids, pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, uid, str(conv.get("title") or "Imported conversation")[:200], "user",
                    None, conv.get("model"), conv.get("system_prompt"),
                    dumps(conv.get("params") or {}), "[]", 1 if conv.get("pinned") else 0,
                    conv.get("created_at") or ts, conv.get("updated_at") or ts,
                ),
            )
            imported["conversations"] += 1
            messages = [m for m in conv.get("messages") or []
                        if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system")]
            # Tree exports (0.4+) carry parent_id; older exports are linear and
            # become a single chain in their listed order.
            is_tree = any("parent_id" in m for m in messages)
            if not is_tree:
                messages.sort(key=lambda m: (int(m.get("position") or 0) if str(m.get("position", "")).lstrip("-").isdigit() else 0))
            ids: dict[str, str] = {}
            depth: dict[str, int] = {}
            pending = list(messages)
            previous: str | None = None
            while pending:  # parents first (exports are ordered by depth, but don't rely on it)
                progressed = False
                for msg in list(pending):
                    old_parent = msg.get("parent_id") if is_tree else previous
                    if is_tree and old_parent and old_parent not in ids:
                        if any(str(m.get("id")) == str(old_parent) for m in pending):
                            continue
                        old_parent = None  # parent missing from the file: becomes a root
                    parent = ids.get(str(old_parent)) if (is_tree and old_parent) else old_parent
                    mid = new_id()
                    status = msg.get("status") if msg.get("status") in ("complete", "error", "cancelled", "interrupted") else "complete"
                    conn.execute(
                        """INSERT INTO messages(id, conversation_id, parent_id, position, role, content, reasoning, status,
                               error, provider_id, model, attachments, sources, metrics, pinned, created_at, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            mid, cid, parent, depth[parent] + 1 if parent else 0, msg["role"],
                            str(msg.get("content") or ""), str(msg.get("reasoning") or ""), status, msg.get("error"),
                            None, msg.get("model"), "[]", dumps(msg.get("sources") or []), dumps(msg.get("metrics") or {}),
                            1 if msg.get("pinned") else 0, msg.get("created_at") or ts, msg.get("updated_at") or ts,
                        ),
                    )
                    depth[mid] = depth[parent] + 1 if parent else 0
                    if msg.get("id") is not None:
                        ids[str(msg["id"])] = mid
                    previous = mid
                    pending.remove(msg)
                    progressed = True
                    imported["messages"] += 1
                if not progressed:  # cycles in a hand-edited file: import the rest as roots
                    for msg in pending:
                        msg["parent_id"] = None
            leaf = ids.get(str(conv.get("active_leaf_id"))) if is_tree else previous
            tree = message_tree.load(state.db, cid)
            if leaf or tree.kids(None):
                message_tree.activate(state.db, cid, leaf or tree.kids(None)[-1], conn=conn, tree=tree)
        for p in payload.get("prompts") or []:
            if not isinstance(p, dict) or not p.get("title") or not p.get("content"):
                continue
            existing = conn.execute("SELECT user_id FROM prompts WHERE id = ?", (str(p.get("id")),)).fetchone() if p.get("id") else None
            if existing and existing[0] == uid:
                imported["skipped"] += 1
                continue
            ts = now_iso()
            conn.execute(
                "INSERT INTO prompts(id, user_id, title, description, category, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (new_id() if existing or not p.get("id") else str(p["id"]), uid, str(p["title"])[:200],
                 str(p.get("description") or "")[:1000], str(p.get("category") or "")[:80], str(p["content"]),
                 p.get("created_at") or ts, ts),
            )
            imported["prompts"] += 1
    return imported


@router.get("/data/backup")
async def backup(admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    tmpdir = Path(tempfile.mkdtemp(prefix="chimera-backup-"))
    target = tmpdir / "chimera.sqlite"
    state.db.backup_to(target)
    return FileResponse(
        target,
        media_type="application/vnd.sqlite3",
        filename=f"chimera-backup-{now_iso()[:10]}.sqlite",
        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
    )


@router.post("/data/restore")
async def restore(file: UploadFile = File(...), admin: dict = Depends(require_admin), state: AppState = Depends(get_state)):
    if state.chat.active:
        raise AppError(409, "generations_running", "Stop running generations before restoring a backup.")
    tmpdir = Path(tempfile.mkdtemp(prefix="chimera-restore-"))
    try:
        target = tmpdir / "restore.sqlite"
        target.write_bytes(await _read_limited(file, 2 * 1024 * 1024 * 1024))
        if not is_chimera_database(target):
            raise AppError(422, "restore_invalid", "This file is not a Chimera database backup.")
        state.db.replace_with(target)
        state.chat.recover_interrupted()
        state.registry.encrypt_legacy_keys()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"restored": True}


def delete_owned_data(state: AppState, user_id: str, *, everything: bool) -> None:
    """Delete a user's data. ``everything`` also removes prompts, files,
    knowledge, settings and their own (unshared) connections."""
    for gen in list(state.chat.active.values()):
        if gen.user["id"] == user_id:
            state.chat.cancel(None, gen.id)
    state.db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
    state.db.execute("DELETE FROM web_sources WHERE user_id = ?", (user_id,))  # also cascades; explicit for clarity
    state.web.forget_user(user_id)
    if everything:
        state.knowledge.delete_user_data(user_id)
        with state.db.transaction() as conn:
            for table in ("prompts", "files"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM providers WHERE user_id = ? AND shared = 0", (user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
        state.files.delete_user_files(user_id)


@router.post("/data/clear")
async def clear_data(body: schemas.ClearData, user: dict = Depends(current_user), state: AppState = Depends(get_state)):
    delete_owned_data(state, user["id"], everything=body.scope == "all")
    return {"cleared": body.scope}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def api_not_found(path: str):
    return JSONResponse({"detail": {"code": "not_found", "message": "Not found", "params": {}}}, status_code=404)


