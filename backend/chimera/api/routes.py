"""HTTP API. All routes live under ``/api``; see docs/API.md for an overview."""

from __future__ import annotations

import hmac
import json
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from .. import __version__
from ..db import dumps, is_chimera_database, new_id, now_iso
from ..providers import ProviderError
from ..security import SESSION_COOKIE
from ..services.chat import ChatError
from ..services.conversations import conversation_out, message_out
from ..services.documents import ExtractionError, extract, safe_filename
from ..services.files import file_out
from ..state import AppState, get_state
from . import schemas

router = APIRouter(prefix="/api")
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


def not_found(what: str = "Not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def chat_error(exc: ChatError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"message": exc.message, "code": exc.code})


# --------------------------------------------------------------------- system
@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@router.get("/info")
async def info(state: AppState = Depends(get_state)):
    return {
        "version": __version__,
        "auth_enabled": state.config.auth_enabled,
        "data_dir": str(state.config.data_dir),
        "database": str(state.config.db_path),
        "db_size": state.config.db_path.stat().st_size if state.config.db_path.exists() else 0,
        "remote_access": state.config.auth_enabled or state.config.allow_unauthenticated_remote,
        "telemetry": False,
    }


# ----------------------------------------------------------------------- auth
@router.get("/auth/status")
async def auth_status(request: Request, state: AppState = Depends(get_state)):
    from ..security import _cookie

    headers = dict(request.scope.get("headers") or [])
    authed = (not state.config.auth_enabled) or state.signer.verify(_cookie(headers, SESSION_COOKIE))
    return {"auth_required": state.config.auth_enabled, "authenticated": authed}


@router.post("/auth/login")
async def login(body: schemas.LoginBody, request: Request, response: Response, state: AppState = Depends(get_state)):
    if not state.config.auth_enabled:
        return {"authenticated": True}
    client = request.client.host if request.client else "unknown"
    wait = state.limiter.blocked(client)
    if wait > 0:
        raise HTTPException(429, detail=f"Too many attempts. Try again in {int(wait) + 1} seconds.")
    if not hmac.compare_digest(body.password.encode(), (state.config.password or "").encode()):
        state.limiter.fail(client)
        raise HTTPException(401, detail="Incorrect password.")
    state.limiter.reset(client)
    response.set_cookie(
        SESSION_COOKIE,
        state.signer.issue(),
        max_age=state.signer.max_age,
        httponly=True,
        samesite="strict",
        secure=state.config.secure_cookies,
        path="/",
    )
    return {"authenticated": True}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


# ------------------------------------------------------------------ settings
@router.get("/settings")
async def get_settings(state: AppState = Depends(get_state)):
    return state.settings.get().model_dump()


@router.patch("/settings")
async def patch_settings(request: Request, state: AppState = Depends(get_state)):
    try:
        patch = await request.json()
    except ValueError as exc:
        raise HTTPException(400, detail="Invalid JSON") from exc
    if not isinstance(patch, dict):
        raise HTTPException(400, detail="Expected an object")
    try:
        return state.settings.update(patch).model_dump()
    except Exception as exc:  # pydantic ValidationError
        errors = getattr(exc, "errors", None)
        raise HTTPException(422, detail=errors() if callable(errors) else str(exc)) from exc


# ----------------------------------------------------------------- providers
@router.get("/providers")
async def list_providers(state: AppState = Depends(get_state)):
    return [state.registry.public(r) for r in state.registry.rows()]


@router.post("/providers", status_code=201)
async def create_provider(body: schemas.ProviderCreate, state: AppState = Depends(get_state)):
    row = state.registry.create(body.model_dump())
    return state.registry.public(row)


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, body: schemas.ProviderUpdate, state: AppState = Depends(get_state)):
    row = state.registry.update(provider_id, body.model_dump(exclude_unset=True))
    if not row:
        raise not_found("Connection not found")
    return state.registry.public(row)


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(provider_id: str, state: AppState = Depends(get_state)):
    if not state.registry.delete(provider_id):
        raise not_found("Connection not found")
    settings = state.settings.get()
    if settings.default_provider_id == provider_id:
        state.settings.update({"default_provider_id": None, "default_model": None})
    return Response(status_code=204)


@router.post("/providers/test")
async def test_unsaved_provider(body: schemas.ProviderTest, state: AppState = Depends(get_state)):
    data = body.model_dump()
    if body.provider_id and body.api_key is None:
        stored = state.registry.row(body.provider_id)
        if stored:
            data["api_key"] = stored.get("api_key")
    return await state.registry.test_unsaved(data)


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, state: AppState = Depends(get_state)):
    try:
        result = await state.registry.test(provider_id)
    except ProviderError as exc:
        raise not_found(exc.message) from exc
    return {**result, "provider": state.registry.public(state.registry.row(provider_id))}


@router.get("/models")
async def list_models(refresh: bool = False, state: AppState = Depends(get_state)):
    return {"providers": await state.registry.discover_all(refresh)}


# ------------------------------------------------------------- conversations
@router.get("/conversations")
async def list_conversations(state: AppState = Depends(get_state)):
    items = state.conversations.list()
    for item in items:
        item["generating"] = item["id"] in state.chat.by_conversation
    return items


@router.get("/conversations/search")
async def search_conversations(q: str = Query("", max_length=500), state: AppState = Depends(get_state)):
    return state.conversations.search(q)


@router.post("/conversations", status_code=201)
async def create_conversation(body: schemas.ConversationCreate, state: AppState = Depends(get_state)):
    data = body.model_dump()
    if body.params is not None:
        data["params"] = body.params.model_dump(exclude_none=True)
    return conversation_out(state.conversations.create(data))


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, state: AppState = Depends(get_state)):
    row = state.conversations.get(cid)
    if not row:
        raise not_found("Conversation not found")
    gen = state.chat.active_for(cid)
    out = conversation_out(row)
    out["messages"] = state.conversations.messages(cid)
    out["generation_id"] = gen.id if gen else None
    if gen:  # expose the freshest partial text
        for m in out["messages"]:
            if m["id"] == gen.message_id:
                m["content"], m["reasoning"] = gen.content, gen.reasoning
    return out


@router.patch("/conversations/{cid}")
async def update_conversation(cid: str, body: schemas.ConversationUpdate, state: AppState = Depends(get_state)):
    if not state.conversations.get(cid):
        raise not_found("Conversation not found")
    data = body.model_dump(exclude_unset=True)
    if "params" in data:
        data["params"] = body.params.model_dump(exclude_none=True) if body.params else {}
    if "title" in data:
        data["title"] = data["title"].strip() or "Untitled"
    return conversation_out(state.conversations.update(cid, data))  # type: ignore[arg-type]


@router.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: str, state: AppState = Depends(get_state)):
    gen = state.chat.active_for(cid)
    if gen:
        state.chat.cancel(gen.id)
    if not state.conversations.delete(cid):
        raise not_found("Conversation not found")
    return Response(status_code=204)


def _stream(state: AppState, gen, initial) -> StreamingResponse:
    stream = state.chat.subscribe(gen, initial)  # attach before launching
    state.chat.launch(gen)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def _start_payload(state: AppState, gen, extra: dict) -> list:
    row = state.conversations.message(gen.message_id)
    return [("start", {"generation_id": gen.id, "conversation_id": gen.conversation_id,
                       "assistant_message": message_out(row), **extra})]  # type: ignore[arg-type]


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, body: schemas.SendMessage, state: AppState = Depends(get_state)):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    try:
        gen, extra = state.chat.prepare_send(cid, body.content, body.attachments, body.provider_id, body.model, params)
    except ChatError as exc:
        raise chat_error(exc) from exc
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.post("/conversations/{cid}/regenerate")
async def regenerate(cid: str, body: schemas.Regenerate, state: AppState = Depends(get_state)):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    try:
        gen, extra = state.chat.prepare_regenerate(cid, body.message_id, body.provider_id, body.model, params)
    except ChatError as exc:
        raise chat_error(exc) from exc
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.post("/conversations/{cid}/messages/{mid}/edit")
async def edit_message(cid: str, mid: str, body: schemas.EditMessage, state: AppState = Depends(get_state)):
    params = body.params.model_dump(exclude_none=True) if body.params else None
    try:
        gen, extra = state.chat.prepare_edit(cid, mid, body.content, body.provider_id, body.model, params)
    except ChatError as exc:
        raise chat_error(exc) from exc
    return _stream(state, gen, _start_payload(state, gen, extra))


@router.get("/conversations/{cid}/stream")
async def reattach(cid: str, state: AppState = Depends(get_state)):
    gen = state.chat.active_for(cid)
    if not gen:
        return Response(status_code=204)
    stream = state.chat.subscribe(gen, [state.chat.snapshot_event(gen)])
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/generations/{gid}/cancel")
async def cancel_generation(gid: str, state: AppState = Depends(get_state)):
    return {"cancelled": state.chat.cancel(gid)}


@router.delete("/messages/{mid}", status_code=204)
async def delete_message(mid: str, state: AppState = Depends(get_state)):
    row = state.conversations.message(mid)
    if not row:
        raise not_found("Message not found")
    if row["status"] == "streaming":
        raise HTTPException(409, detail="Stop the response before deleting it.")
    state.db.execute("DELETE FROM messages WHERE id = ?", (mid,))
    return Response(status_code=204)


# --------------------------------------------------------------------- files
async def _read_limited(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, detail=f"File exceeds the {limit // (1024 * 1024)} MB limit.")
    return data


@router.post("/files", status_code=201)
async def upload_file(file: UploadFile = File(...), state: AppState = Depends(get_state)):
    settings = state.settings.get()
    limit = settings.max_attachment_mb * 1024 * 1024
    data = await _read_limited(file, limit)
    filename = safe_filename(file.filename or "file")
    try:
        extracted = extract(filename, data, limit, settings.max_attachment_chars)
    except ExtractionError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    row = state.files.save(filename, len(data), extracted, data)
    return file_out(row, include_text=False)


@router.get("/files/{fid}")
async def get_file(fid: str, state: AppState = Depends(get_state)):
    row = state.files.get(fid)
    if not row:
        raise not_found("File not found")
    return file_out(row, include_text=True)


@router.get("/files/{fid}/content")
async def get_file_content(fid: str, state: AppState = Depends(get_state)):
    row = state.files.get(fid)
    if not row or row["kind"] != "image":
        raise not_found("File not found")
    path = state.files.path(row)
    if not path:
        raise not_found("File not found")
    return FileResponse(path, media_type=row["mime"], headers={"Content-Disposition": "inline"})


@router.delete("/files/{fid}", status_code=204)
async def delete_file(fid: str, state: AppState = Depends(get_state)):
    if not state.files.delete(fid):
        raise not_found("File not found")
    return Response(status_code=204)


# ------------------------------------------------------------------- prompts
def _prompt_out(row: dict) -> dict:
    return {k: row[k] for k in ("id", "title", "description", "category", "content", "created_at", "updated_at")}


@router.get("/prompts")
async def list_prompts(state: AppState = Depends(get_state)):
    return [_prompt_out(r) for r in state.db.all("SELECT * FROM prompts ORDER BY category, title COLLATE NOCASE")]


@router.post("/prompts", status_code=201)
async def create_prompt(body: schemas.PromptBody, state: AppState = Depends(get_state)):
    pid, ts = new_id(), now_iso()
    state.db.execute(
        "INSERT INTO prompts(id, title, description, category, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (pid, body.title.strip(), body.description.strip(), body.category.strip(), body.content, ts, ts),
    )
    return _prompt_out(state.db.one("SELECT * FROM prompts WHERE id = ?", (pid,)))  # type: ignore[arg-type]


@router.put("/prompts/{pid}")
async def update_prompt(pid: str, body: schemas.PromptBody, state: AppState = Depends(get_state)):
    cur = state.db.execute(
        "UPDATE prompts SET title=?, description=?, category=?, content=?, updated_at=? WHERE id=?",
        (body.title.strip(), body.description.strip(), body.category.strip(), body.content, now_iso(), pid),
    )
    if not cur.rowcount:
        raise not_found("Prompt not found")
    return _prompt_out(state.db.one("SELECT * FROM prompts WHERE id = ?", (pid,)))  # type: ignore[arg-type]


@router.delete("/prompts/{pid}", status_code=204)
async def delete_prompt(pid: str, state: AppState = Depends(get_state)):
    if not state.db.execute("DELETE FROM prompts WHERE id = ?", (pid,)).rowcount:
        raise not_found("Prompt not found")
    return Response(status_code=204)


# ----------------------------------------------------------------- knowledge
@router.get("/collections")
async def list_collections(state: AppState = Depends(get_state)):
    return state.knowledge.collections()


@router.post("/collections", status_code=201)
async def create_collection(body: schemas.CollectionBody, state: AppState = Depends(get_state)):
    return state.knowledge.create_collection(body.name.strip(), body.description.strip())


@router.get("/collections/{cid}")
async def get_collection(cid: str, state: AppState = Depends(get_state)):
    col = state.knowledge.collection(cid)
    if not col:
        raise not_found("Collection not found")
    return {**col, "documents": state.knowledge.documents(cid)}


@router.patch("/collections/{cid}")
async def update_collection(cid: str, body: schemas.CollectionUpdate, state: AppState = Depends(get_state)):
    col = state.knowledge.update_collection(cid, body.model_dump(exclude_unset=True))
    if not col:
        raise not_found("Collection not found")
    return col


@router.delete("/collections/{cid}", status_code=204)
async def delete_collection(cid: str, state: AppState = Depends(get_state)):
    if not state.knowledge.delete_collection(cid):
        raise not_found("Collection not found")
    return Response(status_code=204)


@router.post("/collections/{cid}/documents", status_code=201)
async def add_document(cid: str, file: UploadFile = File(...), state: AppState = Depends(get_state)):
    if not state.knowledge.collection(cid):
        raise not_found("Collection not found")
    settings = state.settings.get()
    limit = max(settings.max_attachment_mb, 50) * 1024 * 1024
    data = await _read_limited(file, limit)
    filename = safe_filename(file.filename or "document")
    try:
        extracted = extract(filename, data, limit, 20_000_000)
    except ExtractionError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    if extracted.kind != "text":
        raise HTTPException(422, detail="Only text documents and PDFs can be added to a collection.")
    return await state.knowledge.add_document(cid, filename, extracted.mime, len(data), extracted.text or "", settings.knowledge)


@router.post("/documents/{doc_id}/reindex")
async def reindex_document(doc_id: str, state: AppState = Depends(get_state)):
    if not state.db.one("SELECT id FROM documents WHERE id = ?", (doc_id,)):
        raise not_found("Document not found")
    await state.knowledge.embed_document(doc_id, state.settings.get().knowledge)
    return state.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, state: AppState = Depends(get_state)):
    if not state.knowledge.delete_document(doc_id):
        raise not_found("Document not found")
    return Response(status_code=204)


@router.post("/collections/{cid}/search")
async def search_collection(cid: str, body: schemas.SearchBody, state: AppState = Depends(get_state)):
    if not state.knowledge.collection(cid):
        raise not_found("Collection not found")
    hits = await state.knowledge.search([cid], body.query, state.settings.get().knowledge, body.top_k)
    return [h.to_dict() for h in hits]


# ---------------------------------------------------------------------- data
EXPORT_VERSION = 1


@router.get("/data/export")
async def export_data(state: AppState = Depends(get_state)):
    conversations = []
    for row in state.db.all("SELECT * FROM conversations ORDER BY created_at"):
        conv = conversation_out(row)
        conv["messages"] = state.conversations.messages(row["id"])
        conversations.append(conv)
    payload = {
        "format": "chimera-export",
        "version": EXPORT_VERSION,
        "exported_at": now_iso(),
        "conversations": conversations,
        "prompts": [_prompt_out(r) for r in state.db.all("SELECT * FROM prompts")],
        "settings": state.settings.get().model_dump(),
        # API keys are deliberately excluded.
        "providers": [
            {k: v for k, v in state.registry.public(r).items() if k in ("id", "name", "kind", "base_url", "api_path", "timeout", "enabled", "vision", "manual_models")}
            for r in state.registry.rows()
        ],
    }
    filename = f"chimera-export-{now_iso()[:10]}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/data/import")
async def import_data(file: UploadFile = File(...), state: AppState = Depends(get_state)):
    raw = await _read_limited(file, 500 * 1024 * 1024)
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(422, detail="Not a valid JSON export.") from exc
    if not isinstance(payload, dict) or payload.get("format") != "chimera-export":
        raise HTTPException(422, detail="This file is not a Chimera export.")
    imported = {"conversations": 0, "messages": 0, "prompts": 0, "skipped": 0}
    with state.db.transaction() as conn:
        for conv in payload.get("conversations") or []:
            if not isinstance(conv, dict) or not conv.get("id"):
                continue
            if conn.execute("SELECT 1 FROM conversations WHERE id = ?", (conv["id"],)).fetchone():
                imported["skipped"] += 1
                continue
            ts = now_iso()
            conn.execute(
                """INSERT INTO conversations(id, title, title_source, provider_id, model, system_prompt, params,
                       collection_ids, pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(conv["id"]), str(conv.get("title") or "Imported conversation")[:200], "user",
                    conv.get("provider_id"), conv.get("model"), conv.get("system_prompt"),
                    dumps(conv.get("params") or {}), "[]", 1 if conv.get("pinned") else 0,
                    conv.get("created_at") or ts, conv.get("updated_at") or ts,
                ),
            )
            imported["conversations"] += 1
            for i, msg in enumerate(conv.get("messages") or []):
                if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant", "system"):
                    continue
                status = msg.get("status") if msg.get("status") in ("complete", "error", "cancelled", "interrupted") else "complete"
                conn.execute(
                    """INSERT INTO messages(id, conversation_id, position, role, content, reasoning, status, error,
                           provider_id, model, attachments, sources, metrics, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        new_id(), str(conv["id"]), int(msg.get("position", i)), msg["role"], str(msg.get("content") or ""),
                        str(msg.get("reasoning") or ""), status, msg.get("error"), msg.get("provider_id"), msg.get("model"),
                        dumps(msg.get("attachments") or []), dumps(msg.get("sources") or []), dumps(msg.get("metrics") or {}),
                        msg.get("created_at") or ts, msg.get("updated_at") or ts,
                    ),
                )
                imported["messages"] += 1
        for p in payload.get("prompts") or []:
            if not isinstance(p, dict) or not p.get("title") or not p.get("content"):
                continue
            if p.get("id") and conn.execute("SELECT 1 FROM prompts WHERE id = ?", (p["id"],)).fetchone():
                imported["skipped"] += 1
                continue
            ts = now_iso()
            conn.execute(
                "INSERT INTO prompts(id, title, description, category, content, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (str(p.get("id") or new_id()), str(p["title"])[:200], str(p.get("description") or "")[:1000],
                 str(p.get("category") or "")[:80], str(p["content"]), p.get("created_at") or ts, ts),
            )
            imported["prompts"] += 1
    return imported


@router.get("/data/backup")
async def backup(state: AppState = Depends(get_state)):
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
async def restore(file: UploadFile = File(...), state: AppState = Depends(get_state)):
    if state.chat.active:
        raise HTTPException(409, detail="Stop running generations before restoring a backup.")
    tmpdir = Path(tempfile.mkdtemp(prefix="chimera-restore-"))
    try:
        target = tmpdir / "restore.sqlite"
        target.write_bytes(await _read_limited(file, 2 * 1024 * 1024 * 1024))
        if not is_chimera_database(target):
            raise HTTPException(422, detail="This file is not a Chimera database backup.")
        state.db.replace_with(target)
        state.chat.recover_interrupted()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return {"restored": True}


@router.post("/data/clear")
async def clear_data(body: schemas.ClearData, state: AppState = Depends(get_state)):
    for gen in list(state.chat.active.values()):
        state.chat.cancel(gen.id)
    with state.db.transaction() as conn:
        conn.execute("DELETE FROM conversations")
        if body.scope == "all":
            for table in ("chunks", "documents", "collections", "prompts", "files", "settings", "providers"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('_seeded', 'true')")
    if body.scope == "all":
        shutil.rmtree(state.config.uploads_dir, ignore_errors=True)
    return {"cleared": body.scope}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
async def api_not_found(path: str):
    return JSONResponse({"detail": "Not found"}, status_code=404)


