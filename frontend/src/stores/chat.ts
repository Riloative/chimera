/**
 * Per-conversation chat state and streaming. Lives outside components so a
 * response keeps streaming while the user navigates elsewhere in the app.
 */
import { create } from "zustand";
import { api, ApiError, stream, type SendBody } from "@/lib/api";
import { errorText } from "@/lib/errors";
import type { Attachment, ConversationDetail, Message, WebSource } from "@/lib/types";
import { useConversations } from "./conversations";
import { useModels } from "./models";
import { useWeb } from "./web";

export interface ConvChat {
  /** Every message of every branch (the message tree). */
  messages: Message[];
  /** Message IDs from the root to the shown leaf. */
  activePath: string[];
  loaded: boolean;
  loadError: string | null;
  generationId: string | null;
  assistantId: string | null;
  streaming: boolean;
  /** Set when a request failed before the server accepted it. */
  sendError: string | null;
  /** Pages read and search results in this conversation (metadata only). */
  webSources: Record<string, WebSource>;
}

interface ChatState {
  convs: Record<string, ConvChat>;
  load: (id: string) => Promise<ConversationDetail | null>;
  send: (id: string, body: SendBody, optimisticAttachments?: Attachment[]) => Promise<void>;
  regenerate: (id: string, messageId?: string, target?: Partial<SendBody>) => Promise<void>;
  edit: (id: string, messageId: string, content: string, target?: Partial<SendBody>) => Promise<void>;
  stop: (id: string) => Promise<void>;
  updateWebSource: (id: string, source: WebSource) => void;
  deleteMessage: (id: string, messageId: string) => Promise<void>;
  switchBranch: (id: string, messageId: string) => Promise<void>;
  setPinned: (id: string, messageId: string, pinned: boolean) => Promise<void>;
  clearSendError: (id: string) => void;
  forget: (id: string) => void;
}

const empty = (): ConvChat => ({
  messages: [],
  activePath: [],
  loaded: false,
  loadError: null,
  generationId: null,
  assistantId: null,
  streaming: false,
  sendError: null,
  webSources: {},
});

const byId = (list: WebSource[] | undefined) => Object.fromEntries((list ?? []).map((s) => [s.id, s]));

const controllers = new Map<string, AbortController>();

// Coalesce token deltas into one state update per animation frame.
const pending = new Map<string, { content: string; reasoning: string }>();
let frame: number | null = null;

export const useChat = create<ChatState>((set, get) => {
  const patchConv = (id: string, fn: (c: ConvChat) => Partial<ConvChat>) =>
    set((s) => {
      const cur = s.convs[id] ?? empty();
      return { convs: { ...s.convs, [id]: { ...cur, ...fn(cur) } } };
    });

  const upsertMessage = (id: string, msg: Message) =>
    patchConv(id, (c) => {
      const idx = c.messages.findIndex((m) => m.id === msg.id);
      if (idx === -1) return { messages: [...c.messages, msg] };
      const messages = c.messages.slice();
      messages[idx] = msg;
      return { messages };
    });

  const flushDeltas = () => {
    frame = null;
    if (!pending.size) return;
    const batch = new Map(pending);
    pending.clear();
    set((s) => {
      const convs = { ...s.convs };
      for (const [convId, delta] of batch) {
        const c = convs[convId];
        if (!c?.assistantId) continue;
        convs[convId] = {
          ...c,
          messages: c.messages.map((m) =>
            m.id === c.assistantId ? { ...m, content: m.content + delta.content, reasoning: m.reasoning + delta.reasoning } : m,
          ),
        };
      }
      return { convs };
    });
  };

  const queueDelta = (convId: string, data: { content?: string; reasoning?: string }) => {
    const cur = pending.get(convId) ?? { content: "", reasoning: "" };
    cur.content += data.content ?? "";
    cur.reasoning += data.reasoning ?? "";
    pending.set(convId, cur);
    if (frame === null) frame = requestAnimationFrame(flushDeltas);
  };

  const onEvent = (convId: string, event: string, data: any) => {
    switch (event) {
      case "start": {
        const asst: Message = data.assistant_message;
        patchConv(convId, (c) => {
          // Edit and regenerate add branches: nothing is removed, the new nodes join the tree.
          let messages = c.messages.filter((m) => !m.id.startsWith("temp-"));
          if (data.user_message) {
            messages = messages.filter((m) => m.id !== data.user_message.id);
            messages.push(data.user_message);
          }
          messages.push(asst);
          return {
            messages,
            activePath: data.active_path ?? c.activePath,
            generationId: data.generation_id,
            assistantId: asst.id,
            streaming: true,
            sendError: null,
          };
        });
        useConversations.getState().upsert({ id: convId, updated_at: asst.created_at, generating: true });
        break;
      }
      case "snapshot":
        if (data.message) {
          upsertMessage(convId, data.message);
          patchConv(convId, (c) => ({
            generationId: data.generation_id,
            assistantId: data.message.id,
            streaming: true,
            webSources: { ...c.webSources, ...byId(data.web_sources) },
          }));
        }
        break;
      case "context":
        patchConv(convId, (c) => ({
          messages: c.messages.map((m) => (m.id === data.message_id ? { ...m, context: data.context } : m)),
        }));
        break;
      case "web":
        patchConv(convId, (c) => ({
          messages: c.messages.map((m) => (m.id === data.message_id ? { ...m, web: data.web } : m)),
          webSources: { ...c.webSources, ...byId(data.sources) },
        }));
        break;
      case "delta":
        queueDelta(convId, data);
        break;
      case "sources":
        patchConv(convId, (c) => ({
          messages: c.messages.map((m) => (m.id === c.assistantId ? { ...m, sources: data.sources } : m)),
        }));
        break;
      case "done":
      case "error":
        flushDeltas();
        if (data.message) upsertMessage(convId, data.message);
        patchConv(convId, () => ({ streaming: false, generationId: null }));
        useConversations.getState().upsert({ id: convId, updated_at: new Date().toISOString(), generating: false });
        // Provider status may have changed (e.g. now verified, or unreachable).
        void useModels.getState().load();
        break;
      case "title":
        useConversations.getState().upsert({ id: convId, title: data.title, title_source: "model" });
        break;
    }
  };

  const run = async (convId: string, path: string, init: { method: string; json?: unknown }) => {
    const controller = new AbortController();
    controllers.set(convId, controller);
    try {
      await stream(path, init, { onEvent: (e, d) => onEvent(convId, e, d) }, controller.signal);
    } finally {
      controllers.delete(convId);
      flushDeltas();
      // The connection closed without a terminal event: resync from the server.
      if (get().convs[convId]?.streaming && !controller.signal.aborted) {
        await get().load(convId);
      }
    }
  };

  const failBeforeStart = (convId: string, err: unknown) => {
    const message = errorText(err);
    // e.g. an administrator turned web access off meanwhile: refresh what the composer offers.
    if (err instanceof ApiError && err.code.startsWith("web.")) void useWeb.getState().load();
    patchConv(convId, (c) => ({
      messages: c.messages.filter((m) => !m.id.startsWith("temp-")),
      activePath: c.activePath.filter((m) => !m.startsWith("temp-")),
      streaming: false,
      sendError: message,
    }));
    throw err;
  };

  return {
    convs: {},

    load: async (id) => {
      try {
        const detail = await api.conversation(id);
        patchConv(id, () => ({
          messages: detail.messages,
          activePath: detail.active_path,
          webSources: byId(detail.web_sources),
          loaded: true,
          loadError: null,
          generationId: detail.generation_id,
          assistantId: detail.generation_id ? detail.messages.findLast((m) => m.status === "streaming")?.id ?? null : null,
          streaming: !!detail.generation_id,
        }));
        useConversations.getState().upsert(detail);
        if (detail.generation_id && !controllers.has(id)) {
          void run(id, `/api/conversations/${id}/stream`, { method: "GET" });
        }
        return detail;
      } catch (err) {
        patchConv(id, () => ({ loaded: true, loadError: errorText(err) }));
        return null;
      }
    },

    send: async (id, body, optimisticAttachments = []) => {
      if (get().convs[id]?.streaming) return;
      const now = new Date().toISOString();
      const conv = get().convs[id];
      const leaf = conv?.messages.find((m) => m.id === conv.activePath.at(-1));
      const temp: Message = {
        id: `temp-${Date.now()}`,
        conversation_id: id,
        parent_id: leaf?.id ?? null,
        position: leaf ? leaf.position + 1 : 0,
        role: "user",
        content: body.content,
        reasoning: "",
        status: "complete",
        error: null,
        provider_id: null,
        model: null,
        attachments: optimisticAttachments,
        sources: [],
        metrics: {},
        created_at: now,
        updated_at: now,
      };
      patchConv(id, (c) => ({ messages: [...c.messages, temp], activePath: [...c.activePath, temp.id], streaming: true, sendError: null, loaded: true }));
      try {
        await run(id, `/api/conversations/${id}/messages`, { method: "POST", json: body });
      } catch (err) {
        failBeforeStart(id, err);
      }
    },

    regenerate: async (id, messageId, target = {}) => {
      if (get().convs[id]?.streaming) return;
      patchConv(id, () => ({ streaming: true, sendError: null }));
      try {
        await run(id, `/api/conversations/${id}/regenerate`, { method: "POST", json: { message_id: messageId ?? null, ...target } });
      } catch (err) {
        failBeforeStart(id, err);
      }
    },

    edit: async (id, messageId, content, target = {}) => {
      if (get().convs[id]?.streaming) return;
      patchConv(id, () => ({ streaming: true, sendError: null }));
      try {
        await run(id, `/api/conversations/${id}/messages/${messageId}/edit`, { method: "POST", json: { content, ...target } });
      } catch (err) {
        failBeforeStart(id, err);
      }
    },

    stop: async (id) => {
      const gen = get().convs[id]?.generationId;
      if (gen) {
        try {
          await api.cancelGeneration(gen);
        } catch {
          /* the stream's terminal event (or a reload) will settle the state */
        }
      }
    },

    updateWebSource: (id, source) => patchConv(id, (c) => ({ webSources: { ...c.webSources, [source.id]: source } })),

    deleteMessage: async (id, messageId) => {
      const result = await api.deleteMessage(messageId);
      // The whole subtree is gone; keep everything else of the tree.
      patchConv(id, (c) => {
        const doomed = new Set(subtreeIds(c.messages, messageId));
        return { messages: c.messages.filter((m) => !doomed.has(m.id)), activePath: result.active_path };
      });
    },

    switchBranch: async (id, messageId) => {
      const result = await api.activateBranch(id, messageId);
      patchConv(id, () => ({ activePath: result.active_path }));
    },

    setPinned: async (id, messageId, pinned) => {
      const updated = await api.updateMessage(messageId, { pinned });
      patchConv(id, (c) => ({ messages: c.messages.map((m) => (m.id === messageId ? { ...m, pinned: updated.pinned } : m)) }));
    },

    clearSendError: (id) => patchConv(id, () => ({ sendError: null })),

    forget: (id) => {
      controllers.get(id)?.abort();
      set((s) => {
        const convs = { ...s.convs };
        delete convs[id];
        return { convs };
      });
    },
  };
});

/** IDs of a message and all its descendants (every branch below it). */
export function subtreeIds(messages: Message[], rootId: string): string[] {
  const children = new Map<string | null, string[]>();
  for (const m of messages) children.set(m.parent_id ?? null, [...(children.get(m.parent_id ?? null) ?? []), m.id]);
  const out: string[] = [];
  const stack = [rootId];
  while (stack.length) {
    const node = stack.pop()!;
    out.push(node);
    stack.push(...(children.get(node) ?? []));
  }
  return out;
}

/** Siblings of a message (same parent), oldest first. */
export function siblingsOf(messages: Message[], message: Message): Message[] {
  return messages
    .filter((m) => (m.parent_id ?? null) === (message.parent_id ?? null) && !m.id.startsWith("temp-"))
    .sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0));
}

/** The messages on the active path, in order. */
export function pathMessages(conv: Pick<ConvChat, "messages" | "activePath"> | undefined): Message[] {
  if (!conv) return [];
  const byId = new Map(conv.messages.map((m) => [m.id, m]));
  return conv.activePath.map((mid) => byId.get(mid)).filter((m): m is Message => !!m);
}
