# Architecture

Chimera is a single process: a FastAPI backend that serves the built React UI
and a JSON/SSE API, with SQLite for storage. No message broker, no second
database, no microservices.

```
 Browser (React SPA, i18next)
   │  same-origin fetch + SSE (session cookie + X-CSRF-Token header)
   ▼
 FastAPI ── SecurityMiddleware (host allow-list, origin check, session → user,
   │                            401 for every non-public /api path, CSRF, CSP)
   ├── api/auth_routes.py ────────── register/login/logout, account, admin users
   ├── api/routes.py ─────────────── chat, connections, files, prompts, knowledge, data
   │      every route: user = Depends(current_user); every query scoped by user_id
   ├── services/auth.py ──────────── users, Argon2id, sessions, throttling, adoption
   │
   ├── services/chat.py ──────────── ChatEngine: builds requests, runs generations
   │       │                          as background tasks, persists partial output,
   │       │                          fans events out to subscribers, cancel/reattach
   │       ├── services/knowledge.py  chunking, FTS5 + embeddings, hybrid retrieval
   │       ├── services/files.py      attachment storage
   │       └── services/settings.py   validated preferences
   │
   ├── services/providers.py ─────── ProviderRegistry: connections, status, discovery
   │       └── providers/            normalized Provider interface
   │             ├── openai_compat.py  llama-server (LlamaCppProvider) + generic
   │             ├── ollama.py         native Ollama
   │             └── sse.py            incremental SSE parser
   │
   └── db.py ─────────────────────── SQLite (WAL), numbered forward-only migrations
```

## Key decisions

**FastAPI + SQLite.** The preferred stack fits well: async I/O for many
concurrent streams, Pydantic validation, and SQLite gives durable storage, FTS5
full-text search and online backups with zero extra services. A single
connection guarded by a lock is plenty for a single-user app (queries take
microseconds; WAL mode).

**Generations are decoupled from HTTP requests.** `ChatEngine` runs each
generation as an `asyncio` task. The request that starts it is just the first
subscriber. Consequences:

- Closing or reloading the tab doesn't cancel the answer; on reload the UI calls
  `GET /api/conversations/{id}/stream`, receives a `snapshot` of the text so far,
  then live deltas.
- Partial output is persisted every 0.5 s, so even a crash keeps most of it.
- *Stop* is an explicit `POST /api/generations/{id}/cancel`, which cancels the
  task; leaving the `httpx` stream context closes the upstream connection and
  llama-server stops decoding.
- On startup, any message still marked `streaming` becomes `interrupted`, so
  nothing is ever stuck loading.
- One generation per conversation at a time (HTTP 409 otherwise) prevents
  duplicate submissions.

**Normalized providers.** UI code never sees a provider payload. Adapters turn
their server's responses into:

```python
class Provider(ABC):
    supported_params: tuple[str, ...]           # which sampling params are forwarded
    async def list_models(self) -> list[ModelInfo]
    async def check(self) -> HealthReport       # health, models, capabilities, per-check results
    def stream_chat(self, ChatRequest) -> AsyncIterator[StreamEvent]  # content | reasoning | usage | done
    async def complete(self, ChatRequest) -> str
    async def embed(self, model, texts) -> list[list[float]]
```

Errors become `ProviderError(message, code)` with codes such as `connection`,
`timeout`, `auth`, `unavailable`, `protocol`, `incomplete`, which drive both the
message shown and whether a connection is marked unreachable.

### Adding a provider

1. Subclass `Provider` (or `OpenAICompatibleProvider` if it speaks that API) in
   `backend/chimera/providers/`.
2. Register it in `PROVIDER_CLASSES` and `DEFAULTS` in `providers/__init__.py`
   and add the kind to `PROVIDER_KINDS` and the `Literal` in `api/schemas.py`.
3. Add it to `KINDS` in `frontend/src/pages/ModelsPage.tsx`.
4. Test it against a mock (see `tests/mock_llama_server.py` and the Ollama test
   using `httpx.MockTransport`).

## Accounts and isolation

- `users`, `sessions` (hashed tokens + hashed CSRF token), `auth_throttle`,
  `user_settings`, `app_settings` (registration mode, connection URL policy).
- Ownership: `user_id` on `conversations`, `prompts`, `files`, `collections`,
  `providers` (plus `shared`). Messages, documents and chunks are owned through
  their parent and always queried via a join or a parent ownership check.
- The middleware authenticates before routing (secure by default); routes use
  `current_user`/`require_admin` and owner-scoped store methods (`owned()`,
  `visible()`), returning 404 for anything else. The generation engine carries
  the user on each `Generation`, so retrieval, attachments, settings and title
  generation are scoped too. See [ENDPOINT_AUDIT.md](ENDPOINT_AUDIT.md).
- Migration 2 adds all of this to existing databases (after an automatic
  backup); the first registered account adopts rows whose `user_id` is NULL.

## Errors and localization

API errors are `{"detail": {"code", "message", "params"}}` (`chimera/errors.py`).
Provider failures carry `code` + `params` (`name`, `status`, server `detail`)
and are stored on messages/connections (`error_code`, `error_params`) so the UI
can render them in the user's language. The UI uses i18next with typed keys;
see [I18N.md](I18N.md).

## Streaming protocol (backend → browser)

`POST /api/conversations/{id}/messages` (also `/regenerate`,
`/messages/{mid}/edit`) responds with `text/event-stream`:

| event | data |
| --- | --- |
| `start` | `generation_id`, `assistant_message`, `user_message` (send/edit) |
| `snapshot` | (reattach only) current partial `message` |
| `sources` | retrieved knowledge excerpts |
| `delta` | `{content}` and/or `{reasoning}` |
| `done` / `error` | final persisted `message`, `error` text |
| `title` | generated conversation title |
| `end` | stream finished |

Comment lines (`: keepalive`) are sent every 15 s. The browser parses the
stream with its own incremental parser (`frontend/src/lib/sse.ts`) and batches
deltas into one render per animation frame.

## Data model

```
providers(id, name, kind, base_url, api_path, api_key, auth_header, timeout, enabled, vision,
          manual_models, cached_models, models_fetched_at, status, last_error, latency_ms,
          checked_at, capabilities, ...)
conversations(id, title, title_source, provider_id, model, system_prompt, params,
              collection_ids, pinned, created_at, updated_at)
messages(id, conversation_id → conversations, position, role, content, reasoning, status,
         error, provider_id, model, attachments, sources, metrics, created_at, updated_at)
files(id, filename, mime, size, kind, text, truncated, storage_name, created_at)
prompts(id, title, description, category, content, ...)
settings(key, value)                     -- JSON values validated by AppSettings
collections(id, name, description, ...)
documents(id, collection_id → collections, filename, chunk_count, embedding_model, warning, ...)
chunks(id, document_id → documents, collection_id, idx, text, embedding BLOB)
chunks_fts                               -- FTS5 external-content index, kept in sync by triggers
schema_version(version)
```

Message `status`: `streaming → complete | error | cancelled | interrupted`.
`title_source`: `default → fallback (first message) → model`, or `user` once
renamed (a model title never overwrites a user's).

Generation parameters merge in order: global defaults → conversation → request;
`null` means "server default", and only parameters in the provider's
`supported_params` are sent.

## Retrieval (RAG)

1. Documents are extracted (text/PDF), split by paragraph → line → sentence →
   word into ~`chunk_size` characters with overlap.
2. Chunks are indexed in FTS5 (porter stemming) immediately.
3. If an embedding model is configured, chunks are embedded in batches through
   any connection's embeddings endpoint, normalized and stored as float32 blobs.
4. At question time: BM25 keyword hits and cosine-similarity hits are merged with
   reciprocal-rank fusion; the top *k* are added to the system prompt with
   numbered citations and sent to the UI as `sources`.

Embedding failures never block ingestion; documents fall back to keyword search
with a visible warning.

## Frontend

- React 19, TypeScript, Vite, Tailwind CSS v4 (design tokens as CSS variables,
  light/dark), Radix primitives for accessible menus/dialogs/tooltips/popovers,
  lucide icons, zustand stores.
- Streaming state lives in `stores/chat.ts`, outside components, so a response
  keeps streaming while you navigate.
- Markdown: `react-markdown` + GFM + KaTeX + highlight.js, no raw HTML.
- Fonts (Inter, JetBrains Mono) and KaTeX fonts are bundled via `@fontsource`.
- The theme is applied before first paint by `public/theme-init.js` (an external
  file so the CSP needs no `unsafe-inline` scripts).
