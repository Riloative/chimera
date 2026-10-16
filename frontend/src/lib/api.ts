/** Thin, typed client for the Chimera backend (same origin only). */

import { readSSE } from "./sse";
import type {
  AppSettings,
  UpdateAvailable,
  UpdateView,
  Collection,
  Conversation,
  ConversationDetail,
  ContextEstimate,
  Message,
  GenerationParams,
  KnowledgeDocument,
  Prompt,
  Provider,
  ProviderModels,
  SearchHit,
  TestResult,
  Attachment,
  AuthStatus,
  User,
  SessionInfo,
  AdminSettings,
  WebAdminView,
  WebAdminSettings,
  WebEngineKind,
  WebEngineTest,
  WebSource,
  WebStatus,
} from "./types";

/** An API failure with a stable, translatable code (see lib/errors.ts). */
export class ApiError extends Error {
  status: number;
  code: string;
  params: Record<string, unknown>;
  constructor(message: string, status: number, code = "unknown", params: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.params = params;
  }
}

type AuthEvent = "unauthorized" | "password_change_required";
type Listener = (event: AuthEvent) => void;
const authListeners = new Set<Listener>();
export function onAuthEvent(fn: Listener) {
  authListeners.add(fn);
  return () => {
    authListeners.delete(fn);
  };
}

/** The double-submit CSRF token: readable cookie echoed in a header. */
export function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)chimera_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function baseHeaders(): Record<string, string> {
  const token = csrfToken();
  return token ? { "X-CSRF-Token": token } : {};
}

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let code = `http_${res.status}`;
  let params: Record<string, unknown> = {};
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      code = String(detail.code);
      message = String(detail.message ?? message);
      params = (detail.params as Record<string, unknown>) ?? {};
    } else if (typeof detail === "string") message = detail;
  } catch {
    /* non-JSON error */
  }
  if (res.status === 401 && code === "auth_required") authListeners.forEach((fn) => fn("unauthorized"));
  if (res.status === 403 && code === "password_change_required") authListeners.forEach((fn) => fn("password_change_required"));
  return new ApiError(message, res.status, code, params);
}

export async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(path, {
      ...rest,
      headers: { ...baseHeaders(), ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("Cannot reach the Chimera server. Is it running?", 0, "network");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface StreamHandlers {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- SSE payloads differ per event; handled per event type
  onEvent: (event: string, data: any) => void;
}

/** POST/GET an SSE endpoint and dispatch events until the stream closes. */
export async function stream(path: string, init: RequestInit & { json?: unknown }, handlers: StreamHandlers, signal?: AbortSignal) {
  const { json, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(path, {
      ...rest,
      signal,
      headers: { ...baseHeaders(), Accept: "text/event-stream", ...(json !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: json !== undefined ? JSON.stringify(json) : undefined,
      credentials: "same-origin",
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return false;
    throw new ApiError("Cannot reach the Chimera server. Is it running?", 0, "network");
  }
  if (res.status === 204) return false;
  if (!res.ok) throw await parseError(res);
  if (!res.body) throw new ApiError("Empty response from server", res.status);
  try {
    for await (const msg of readSSE(res.body, signal)) {
      let data: unknown = null;
      try {
        data = JSON.parse(msg.data);
      } catch {
        continue; // ignore malformed events
      }
      handlers.onEvent(msg.event, data);
    }
  } catch (err) {
    if ((err as Error).name === "AbortError") return false;
    throw err;
  }
  return true;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  info: () =>
    request<{
      version: string;
      commit: string | null;
      telemetry: boolean;
      registration_mode: string;
      data_dir?: string;
      database?: string;
      db_size?: number;
      users?: number;
      update?: UpdateAvailable | null;
    }>("/api/info"),
  adminUpdates: () => request<UpdateView>("/api/admin/updates"),
  setUpdateCheck: (enabled: boolean) => request<UpdateView>("/api/admin/updates", { method: "PATCH", json: { enabled } }),

  authStatus: () => request<AuthStatus>("/api/auth/status"),
  register: (data: { email: string; username: string; password: string; password_confirm: string; language?: string; setup_token?: string; remember?: boolean }) =>
    request<User>("/api/auth/register", { method: "POST", json: data }),
  login: (data: { identifier: string; password: string; remember?: boolean }) => request<User>("/api/auth/login", { method: "POST", json: data }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request<User>("/api/auth/me"),
  updateAccount: (data: { email?: string; username?: string; language?: string; current_password?: string }) =>
    request<User>("/api/account", { method: "PATCH", json: data }),
  changePassword: (data: { current_password: string; new_password: string; new_password_confirm: string }) =>
    request<User>("/api/account/password", { method: "POST", json: data }),
  sessions: () => request<SessionInfo[]>("/api/account/sessions"),
  revokeSession: (id: string) => request<void>(`/api/account/sessions/${id}`, { method: "DELETE" }),
  revokeOtherSessions: () => request<{ revoked: number }>("/api/account/sessions/revoke-others", { method: "POST" }),
  deleteAccount: (password: string) => request<void>("/api/account", { method: "DELETE", json: { password } }),
  dismissNotice: () => request<void>("/api/account/notice/dismiss", { method: "POST" }),

  adminUsers: () => request<User[]>("/api/admin/users"),
  adminCreateUser: (data: { email: string; username: string; password?: string; role: "admin" | "user" }) =>
    request<User & { temporary_password: string | null }>("/api/admin/users", { method: "POST", json: data }),
  adminUpdateUser: (id: string, data: { role?: "admin" | "user"; active?: boolean }) =>
    request<User>(`/api/admin/users/${id}`, { method: "PATCH", json: data }),
  adminResetPassword: (id: string) => request<{ temporary_password: string }>(`/api/admin/users/${id}/reset-password`, { method: "POST" }),
  adminDeleteUser: (id: string) => request<void>(`/api/admin/users/${id}`, { method: "DELETE" }),
  adminSettings: () => request<AdminSettings>("/api/admin/settings"),
  adminUpdateSettings: (data: Partial<AdminSettings>) => request<AdminSettings>("/api/admin/settings", { method: "PATCH", json: data }),

  webStatus: () => request<WebStatus>("/api/web/status"),
  webSource: (id: string) => request<WebSource & { content: string }>(`/api/web/sources/${id}`),
  refetchWebSource: (id: string, loader: "auto" | "static" | "browser" = "auto") =>
    request<{ ok: boolean; source: WebSource; error_code?: string | null; error_params?: Record<string, unknown> }>(`/api/web/sources/${id}/refetch`, {
      method: "POST",
      json: { loader },
    }),
  adminWeb: () => request<WebAdminView>("/api/admin/web"),
  adminUpdateWeb: (data: { settings?: Partial<WebAdminSettings>; engine_keys?: Partial<Record<WebEngineKind, string | null>> }) =>
    request<WebAdminView>("/api/admin/web", { method: "PATCH", json: data }),
  adminTestWeb: (data: { kind: WebEngineKind; searxng_url?: string; searxng_auth_header?: string; api_key?: string; query?: string }) =>
    request<WebEngineTest>("/api/admin/web/test", { method: "POST", json: data }),

  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (patch: Partial<AppSettings> | Record<string, unknown>) =>
    request<AppSettings>("/api/settings", { method: "PATCH", json: patch }),

  providers: () => request<Provider[]>("/api/providers"),
  createProvider: (data: Record<string, unknown>) => request<Provider>("/api/providers", { method: "POST", json: data }),
  updateProvider: (id: string, data: Record<string, unknown>) =>
    request<Provider>(`/api/providers/${id}`, { method: "PATCH", json: data }),
  deleteProvider: (id: string) => request<void>(`/api/providers/${id}`, { method: "DELETE" }),
  testProvider: (id: string) => request<TestResult>(`/api/providers/${id}/test`, { method: "POST" }),
  testUnsaved: (data: Record<string, unknown>) => request<TestResult>("/api/providers/test", { method: "POST", json: data }),
  models: (refresh = false) => request<{ providers: ProviderModels[] }>(`/api/models${refresh ? "?refresh=true" : ""}`),

  conversations: () => request<Conversation[]>("/api/conversations"),
  searchConversations: (q: string) => request<Conversation[]>(`/api/conversations/search?q=${encodeURIComponent(q)}`),
  createConversation: (data: Record<string, unknown>) => request<Conversation>("/api/conversations", { method: "POST", json: data }),
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  updateConversation: (id: string, data: Record<string, unknown>) =>
    request<Conversation>(`/api/conversations/${id}`, { method: "PATCH", json: data }),
  deleteConversation: (id: string) => request<void>(`/api/conversations/${id}`, { method: "DELETE" }),
  deleteMessage: (id: string) => request<{ deleted: number; active_path: string[] }>(`/api/messages/${id}`, { method: "DELETE" }),
  updateMessage: (id: string, data: { pinned: boolean }) => request<Message>(`/api/messages/${id}`, { method: "PATCH", json: data }),
  contextEstimate: (conversationId: string) => request<ContextEstimate>(`/api/conversations/${conversationId}/context`),
  activateBranch: (conversationId: string, messageId: string) =>
    request<{ active_leaf_id: string; active_path: string[] }>(`/api/conversations/${conversationId}/active`, {
      method: "POST",
      json: { message_id: messageId },
    }),
  cancelGeneration: (id: string) => request<{ cancelled: boolean }>(`/api/generations/${id}/cancel`, { method: "POST" }),

  uploadFile: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Attachment>("/api/files", { method: "POST", body: form });
  },
  file: (id: string) => request<Attachment & { text: string | null }>(`/api/files/${id}`),
  deleteFile: (id: string) => request<void>(`/api/files/${id}`, { method: "DELETE" }),

  prompts: () => request<Prompt[]>("/api/prompts"),
  createPrompt: (data: Partial<Prompt>) => request<Prompt>("/api/prompts", { method: "POST", json: data }),
  updatePrompt: (id: string, data: Partial<Prompt>) => request<Prompt>(`/api/prompts/${id}`, { method: "PUT", json: data }),
  deletePrompt: (id: string) => request<void>(`/api/prompts/${id}`, { method: "DELETE" }),

  collections: () => request<Collection[]>("/api/collections"),
  collection: (id: string) => request<Collection & { documents: KnowledgeDocument[] }>(`/api/collections/${id}`),
  createCollection: (data: { name: string; description?: string }) =>
    request<Collection>("/api/collections", { method: "POST", json: data }),
  updateCollection: (id: string, data: { name?: string; description?: string }) =>
    request<Collection>(`/api/collections/${id}`, { method: "PATCH", json: data }),
  deleteCollection: (id: string) => request<void>(`/api/collections/${id}`, { method: "DELETE" }),
  addDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<KnowledgeDocument>(`/api/collections/${id}/documents`, { method: "POST", body: form });
  },
  deleteDocument: (id: string) => request<void>(`/api/documents/${id}`, { method: "DELETE" }),
  reindexDocument: (id: string) => request<KnowledgeDocument>(`/api/documents/${id}/reindex`, { method: "POST" }),
  searchCollection: (id: string, query: string) =>
    request<SearchHit[]>(`/api/collections/${id}/search`, { method: "POST", json: { query } }),

  importData: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ conversations: number; messages: number; prompts: number; skipped: number }>("/api/data/import", {
      method: "POST",
      body: form,
    });
  },
  restore: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ restored: boolean }>("/api/data/restore", { method: "POST", body: form });
  },
  clearData: (scope: "conversations" | "all") =>
    request<{ cleared: string }>("/api/data/clear", { method: "POST", json: { scope, confirm: "DELETE" } }),
};

export interface SendBody {
  content: string;
  attachments?: string[];
  provider_id?: string | null;
  model?: string | null;
  params?: GenerationParams | null;
  /** Links in the message to read (null: the user's default). */
  urls?: string[] | null;
  web_search?: boolean | null;
  timezone?: string | null;
}
