# API endpoint audit (accounts & data isolation)

Every route under `/api` was reviewed when accounts were introduced, again
when web reading and search were added (version 0.3), and again for
conversation branching, context management and the headless browser (version
0.4: four new routes and two changed ones, marked **0.4**), and for the 1.0
release (one new public route, two new admin routes, three changed ones,
marked **1.0**). The table lists every registered route with how access is enforced;
`tests/test_security.py` enumerates the live route table, so new routes are
covered automatically.

**How enforcement works**

1. `SecurityMiddleware` runs *before routing*. Any `/api/*` path that is not in
   the 4-path public allow-list gets `401 auth_required` without a valid
   session, so a new route is private even if its author forgets a dependency.
   State-changing methods additionally need a valid CSRF token.
2. Each route takes `user = Depends(current_user)` (or `require_admin`) and
   every query filters on `user_id`, or joins to a table that does.
3. Records owned by someone else are reported as missing (**404**), never 403,
   so their existence isn't revealed. Admin endpoints also answer 404 to
   non-admins.
4. `tests/test_security.py::test_every_api_route_requires_a_session`
   enumerates the route table and asserts `401` for every non-public route, and
   `tests/test_isolation.py` exercises cross-user access on each resource type.

Legend: **Public** – no session needed · **User** – signed-in user, scoped to
their own data · **Admin** – administrators only (404 for others).

| Method | Path | Access | Ownership / scoping |
| --- | --- | --- | --- |
| GET | `/api/health` **1.0** | Public | Liveness: `{"status":"ok","version":"…"}`; the version only (no commit, paths or counts) |
| GET | `/api/ready` **1.0** | Public | Readiness for health checks: `{"status","checks":{"migrations","writable","data_dir"}}`, booleans only; 503 when not ready. Takes and immediately releases the database write lock |
| GET | `/api/auth/status` | Public | Reveals only whether setup/registration is open; sets anonymous CSRF cookie |
| POST | `/api/auth/register` | Public | Registration policy, per-IP rate limit, CSRF (pre-session) |
| POST | `/api/auth/login` | Public | Per-IP + per-account throttling, generic errors, CSRF (pre-session), session rotation |
| POST | `/api/auth/logout` | Public | Revokes only the caller's own session (if any) |
| GET | `/api/auth/me` | User | Own profile |
| PATCH | `/api/account` | User | Own profile; email/login changes need current password |
| POST | `/api/account/password` | User | Needs current password; revokes other sessions |
| GET | `/api/account/sessions` | User | `WHERE user_id = me` |
| DELETE | `/api/account/sessions/{sid}` | User | `WHERE id = ? AND user_id = me` → 404 otherwise |
| POST | `/api/account/sessions/revoke-others` | User | Own sessions only |
| POST | `/api/account/notice/dismiss` | User | Own notice |
| DELETE | `/api/account` | User | Needs password; last-admin guard; deletes only own data |
| GET | `/api/admin/users` | Admin | — |
| POST | `/api/admin/users` | Admin | — |
| PATCH | `/api/admin/users/{uid}` | Admin | Last-admin guard; disabling revokes sessions |
| POST | `/api/admin/users/{uid}/reset-password` | Admin | Temporary password, forced change, sessions revoked |
| DELETE | `/api/admin/users/{uid}` | Admin | Cannot delete self; last-admin guard |
| GET/PATCH | `/api/admin/settings` | Admin | Registration mode, connection URL policy |
| GET | `/api/info` **1.0** | User | Version and build commit for everyone; paths, DB size, user count and the available update (if the admin turned the check on) only for admins |
| GET | `/api/admin/updates` **1.0** | Admin | Update-check setting and last result (time, ok, error code, newest release and its GitHub URL) |
| PATCH | `/api/admin/updates` **1.0** | Admin | `{enabled}` only (strict schema); CSRF; turning it on runs one check; checks happen at most once a day through the hardened fetcher to the operator-configured `CHIMERA_UPDATE_CHECK_URL`, with no user or instance data; nothing is ever installed |
| GET/PATCH | `/api/settings` | User | `user_settings WHERE user_id = me` (includes personal web preferences) |
| GET | `/api/web/status` **new** | User | Instance-wide switch, whether search is available, limits; no secrets or addresses |
| GET | `/api/web/sources/{sid}` **new** | User | `WHERE id = ? AND user_id = me` → 404; returns extracted text, never raw bytes |
| POST | `/api/web/sources/{sid}/refetch` **0.4** | User | Owned source → 404; CSRF; 403 `web.disabled` when web access is off; 409 while that conversation is generating; counts against the hourly fetch quota; only re-reads the stored URL (no caller-supplied URL). New optional body `{loader: auto\|static\|browser}`: `browser` → 503 `web.browser_unavailable` unless the browser is configured, switched on and reachable; renders count against the per-user render quota (`web.browser_limit`); the URL is validated before the browser is asked and every connection the browser makes is filtered by the egress proxy |
| GET/PATCH | `/api/admin/web` **0.4** | Admin | Web settings; engine keys write-only (`has_key` only), encrypted at rest. Now also returns the browser service status (configured / reachable / active renders); never its URL or secret |
| POST | `/api/admin/web/test` **new** | Admin | Runs one real search against the given engine; a typed SearXNG URL is validated; a stored key is used if none is typed |
| GET | `/api/providers` | User | Own connections + shared ones |
| POST | `/api/providers` | User | Owner = me; `shared` admin-only; URL policy for non-admins |
| PATCH/DELETE | `/api/providers/{id}` | User | Visible → else 404; own or (shared and admin) → else 403 read-only |
| POST | `/api/providers/test` | User | URL policy; stored key reused only if the caller may edit that connection |
| POST | `/api/providers/{id}/test` | User | Visible connections only |
| GET | `/api/models` | User | Discovery over own + shared connections; cache holds server facts only |
| GET/POST | `/api/conversations` | User | `WHERE user_id = me`; provider/collection references filtered to visible/owned; GET pages with optional `limit` (1–500) and `offset` |
| GET | `/api/conversations/search` | User | `WHERE c.user_id = me` (titles and message content) |
| GET/PATCH/DELETE | `/api/conversations/{cid}` | User | `owned()` → 404; `context_strategy` validated (`sliding\|summarize\|off\|null`) |
| GET | `/api/conversations/{cid}/context` **0.4** | User | Owned conversation → 404. Dry run of the next request on the active branch: counts tokens (llama-server `/tokenize` on a *visible* connection) and uses cached summaries only — never calls the model |
| POST | `/api/conversations/{cid}/active` **0.4** | User | Owned conversation → 404, and the message must belong to *that* conversation (`branch.not_found`, 404); CSRF; allowed while streaming (only changes what is shown) |
| POST | `/api/conversations/{cid}/messages` | User | Owned conversation; attachments must be own files; provider must be visible; `urls` must appear in the message (others ignored); `web_search`/`urls` → 403 `web.disabled` when web access is off |
| POST | `/api/conversations/{cid}/regenerate` | User | Owned conversation |
| POST | `/api/conversations/{cid}/messages/{mid}/edit` | User | Owned conversation and message |
| GET | `/api/conversations/{cid}/stream` | User | Owned conversation; generation's owner checked |
| POST | `/api/generations/{gid}/cancel` | User | Generation's `user_id` must equal caller → 404 |
| DELETE | `/api/messages/{mid}` **0.4** | User | Message joined to an owned conversation → 404. Now deletes the message's whole subtree (every branch below it) in one transaction; 409 `message_streaming` if any node in it is still generating; returns the repaired active path |
| PATCH | `/api/messages/{mid}` **0.4** | User | Message joined to an owned conversation → 404; only `{pinned}` can change (strict schema) |
| POST | `/api/files` | User | Stored under `uploads/<user_id>/<random id>` |
| GET/DELETE | `/api/files/{fid}` | User | `WHERE id = ? AND user_id = me` |
| GET | `/api/files/{fid}/content` | User | Owned + path must resolve inside the owner's directory |
| GET/POST | `/api/prompts` | User | `WHERE user_id = me` |
| PUT/DELETE | `/api/prompts/{pid}` | User | Owned → 404 |
| GET/POST | `/api/collections` | User | `WHERE user_id = me` |
| GET/PATCH/DELETE | `/api/collections/{cid}` | User | Owned → 404 |
| POST | `/api/collections/{cid}/documents` | User | Owned collection; embeddings via a visible connection |
| POST | `/api/collections/{cid}/search` | User | Owned collection; retrieval re-filters collection IDs to owned |
| POST | `/api/documents/{id}/reindex` | User | Document joined to an owned collection → 404 |
| DELETE | `/api/documents/{id}` | User | Same |
| GET | `/api/data/export` | User | Own conversations, prompts, settings, own connections (no keys) |
| POST | `/api/data/import` | User | Imported into the caller; IDs owned by others get fresh IDs |
| GET | `/api/data/backup` | Admin | Whole database (all accounts) |
| POST | `/api/data/restore` **1.0** | Admin | Whole database; now refuses (422 `restore_too_new`) a backup with a newer schema than the running version, before touching the live database |
| POST | `/api/data/clear` | User | Deletes only the caller's data (shared connections are kept) |
| * | `/api/{path}` | User | Catch-all 404 (still behind the auth middleware) |

Internal paths that also had to be scoped (no route of their own):

- **Generation engine** – each generation carries its user; the request
  builder reads attachments with `files.owned(user)`, retrieval with the user's
  collections only, the provider with `registry.visible(user)`, and settings
  from that user. Title generation uses the same user and conversation.
- **Knowledge search** – `KnowledgeBase.search()` drops any collection ID the
  user doesn't own before querying, so no caller can widen the scope.
- **Model list cache** – stored per connection; contains only what the server
  reported (model IDs, capabilities), never user content.
- **Web pipeline** – runs inside the user's generation: pages are stored with
  that user's ID on that conversation's messages; the read cache and `robots.txt`
  cache are keyed by user; quotas are per user; the composed prompt only
  includes sources of the conversation being answered. Account deletion and
  *Delete all my data* remove `web_sources` rows and the user's cache entries.
- **Context management** – summaries (`context_summaries`) carry the user and
  conversation and are looked up by both; they're written only by that user's
  own generations, use a model on a connection visible to that user, and are
  deleted with the conversation or account. The token-count cache is keyed by
  connection, model and a hash of the text (no user content is retrievable
  from it). Pinning and the context strategy are per conversation of the
  owner.
- **Headless browser** – the service is not an API route: it is reachable only
  on the internal Compose network and needs the shared secret. Each render
  gets single-use egress-proxy credentials bound to the requesting user's
  render, revoked when it ends; the browser context is fresh per render, so
  no cookies or storage cross users. Rendered text is stored like any other
  source of that user.
- **Branches** – the tree helpers only ever load messages of one owned
  conversation; switching or deleting validates that the target message is in
  it. Search returns `match_message_id` only for the caller's conversations.
  Import of a tree re-checks every parent reference inside the imported
  conversation and maps it to fresh IDs (a parent missing from the file makes
  the message a root); IDs can never point into another conversation.
