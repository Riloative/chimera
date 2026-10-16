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
   │       ├── web/pipeline.py        per-turn link reading, query writing, search,
   │       │                          context budgeting, step events (docs/WEB.md)
   │       ├── services/files.py      attachment storage
   │       └── services/settings.py   validated preferences
   │
   ├── api/web_routes.py ─────────── web status, stored sources (+ re-read), admin web settings
   ├── web/ ──────────────────────── fetcher.py (SSRF-hardened HTTP, the only outbound web path),
   │                                  netguard.py, extract.py (trafilatura), search.py (SearXNG,
   │                                  Brave, Tavily), rank.py (BM25), prompting.py, service.py
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
| `start` | `generation_id`, `assistant_message`, `user_message` (send/edit), `active_path` (the branch now shown) |
| `snapshot` | (reattach only) current partial `message` |
| `sources` | retrieved knowledge excerpts |
| `web` | `message_id`, full current `web` state (steps, queries, citation map) and source metadata; sent on every change and replayed on reattach |
| `context` | `message_id`, how the request was built (window and its source, tokens used per part, messages left out, where history starts, summary); sent before the first token |
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
              collection_ids, pinned, active_leaf_id, context_strategy, context_state,
              created_at, updated_at)
messages(id, conversation_id → conversations, parent_id, active_child_id, position (= depth),
         role, content, reasoning, status, error, provider_id, model, attachments, sources,
         metrics, web, pinned, context, created_at, updated_at)
context_summaries(id, user_id, conversation_id, covers_hash, last_message_id, message_count,
                  content, model, created_at)
web_sources(id, user_id, conversation_id, message_id → messages, origin pasted|search, url,
            final_url, title, published, status ok|snippet|error, error_code, content, rank,
            loader static|browser, ...)
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

## Conversation tree

Messages form a tree (`parent_id`; the first message has none). Editing a
message adds a **sibling** user message (with a copy of its attachments) and a
new answer under it; regenerating adds a sibling answer under the same user
message. Nothing is overwritten. Each node remembers its `active_child_id`, and
the conversation its `active_leaf_id`; the **active path** (root → leaf) is
what is shown and what is sent to the model. Switching a branch
(`POST /api/conversations/{id}/active`) activates the newest leaf below the
chosen message and is allowed while another branch streams (the stream keeps
writing to its own node).

- `parent_id` deliberately has no foreign-key cascade: SQLite's recursive
  trigger depth (~1000) would cap conversation length. Deleting a message
  deletes its subtree explicitly in one transaction (refused while any node in
  it streams) and repairs the active path to the parent's newest remaining
  branch.
- Migration 4 turned linear histories into chains (ordered by position, time,
  rowid) after the usual pre-migration backup.
- Titles come from the first exchange only, search covers all branches (a hit
  activates its branch), and export version 3 writes the tree; version 1–2
  (linear) files still import as chains.

## Context management

`backend/chimera/services/context.py`. For each request:

```
available = (window − reserved for the answer − safety margin)
```

- **Window**: per-connection override → what the server reports
  (llama-server `/props` → `default_generation_settings.n_ctx`, which is the
  *per-slot* size, `-c ÷ --parallel`) → the user's fallback (default 4096).
- **Reserve**: `max_tokens` when set, else `min(reserve_tokens, window/4)`
  (default 1024). **Margin**: 5 % of the window.
- **Counting**: llama-server's `/tokenize` (exact; cached per text and model,
  LRU) plus the chat template's per-message overhead measured once with
  `/apply-template`; the final request is verified with one exact count of the
  rendered prompt and tightened if needed. Other servers: ≈3.5 characters per
  token plus a fixed overhead; images count 768 tokens.
- **Priority**: system prompt (plus summary) → the latest user message with its
  attachments, knowledge excerpts and web pages → pinned exchanges → recent
  history newest first, in **whole exchanges** (a user message and its
  answers). If system + latest don't fit: `context.too_large`, nothing sent.
- **Hysteresis**: when history must be cut, it is cut to 75 % of its budget
  and the cut point (`conversations.context_state.cut_id`) is kept while
  everything after it still fits, so the prompt prefix stays identical and
  llama-server's prompt cache is reused; the cut only moves when needed again.
- **Strategies**: `sliding` (default) leaves out older exchanges; `summarize`
  replaces them with a model-written summary (≤ 20 % of the history budget,
  cached in `context_summaries` by the hash of the covered message ids and
  extended incrementally; on failure it falls back to sliding and says so);
  `off` sends the whole branch. Per user (Settings → Chat), overridable per
  conversation.
- **Server overflow**: if llama-server still answers
  `exceed_context_size_error` before any token, the request is rebuilt once
  against the size in the error and 70 % of the budget; a second rejection is
  shown (`context_exceeded`). Never retried with `off`.
- **Web** text is part of the latest message: the pipeline composes it for the
  room left after the system prompt, and history is trimmed before sources are
  dropped (`history_trimmed` step).
- What was done is stored on the answer (`messages.context`) and shown: a
  usage indicator, and a marker where history stops being sent.

## Operations

- **Version**: one source, `backend/pyproject.toml`; `chimera.__version__`
  reads it (or the installed package metadata in the image).
  `scripts/check-version.py` keeps `frontend/package.json`, the Compose image
  tags, `CHANGELOG.md` and release tags consistent (CI).
- **Startup** (`chimera/__main__.py`): validate the configuration
  (`config.validate_config`: fatal errors exit 2, risky settings warn), refuse
  a database whose schema is newer than the code (exit 3), then serve.
  `chimera <command>` dispatches to the administrative CLI (`cli.py`,
  `maintenance.py`: backup/restore archives).
- **Lifespan**: an exclusive lock on `<data>/.chimera.lock` (one server per
  data directory; `restore` refuses while it is held), migrations with a
  pre-migration copy, the opt-in update checker (`services/updates.py`); on
  shutdown running generations get `CHIMERA_SHUTDOWN_GRACE_SECONDS` and are
  then saved as `interrupted`.
- **Health**: `/api/health` (liveness + version) and `/api/ready` (migrations
  complete, database writable, data directory writable) for container health
  checks.
- **Logs**: `chimera/logs.py` (text or JSON; uvicorn uses the same config;
  access lines without query strings).

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
