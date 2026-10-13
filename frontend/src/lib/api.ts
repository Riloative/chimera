/** Thin, typed client for the Chimera backend (same origin only). */

import { readSSE } from "./sse";
import type {
  AppSettings,
  Collection,
  Conversation,
  ConversationDetail,
  GenerationParams,
  KnowledgeDocument,
  Prompt,
  Provider,
  ProviderModels,
  SearchHit,
  TestResult,
  Attachment,
} from "./types";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type Listener = () => void;
const unauthorizedListeners = new Set<Listener>();
export function onUnauthorized(fn: Listener) {
  unauthorizedListeners.add(fn);
  return () => {
    unauthorizedListeners.delete(fn);
  };
}

const BASE_HEADERS = { "X-Chimera-Request": "1" };

async function parseError(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  let code: string | undefined;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object" && "message" in detail) {
      message = String(detail.message);
      code = detail.code;
    } else if (Array.isArray(detail) && detail[0]?.msg) message = detail[0].msg;
    code = code ?? body?.code;
  } catch {
    /* non-JSON error */
  }
  if (res.status === 401) unauthorizedListeners.forEach((fn) => fn());
  return new ApiError(message, res.status, code);
}

export async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(path, {
      ...rest,
      headers: { ...BASE_HEADERS, ...(json !== undefined ? { "Content-Type": "application/json" } : {}), ...headers },
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
      headers: { ...BASE_HEADERS, Accept: "text/event-stream", ...(json !== undefined ? { "Content-Type": "application/json" } : {}) },
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
  health: () => request<{ status: string; version: string }>("/api/health"),
  info: () =>
    request<{ version: string; auth_enabled: boolean; data_dir: string; database: string; db_size: number; remote_access: boolean; telemetry: boolean }>(
      "/api/info",
    ),
  authStatus: () => request<{ auth_required: boolean; authenticated: boolean }>("/api/auth/status"),
  login: (password: string) => request<{ authenticated: boolean }>("/api/auth/login", { method: "POST", json: { password } }),
  logout: () => request("/api/auth/logout", { method: "POST" }),

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
  deleteMessage: (id: string) => request<void>(`/api/messages/${id}`, { method: "DELETE" }),
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
}
