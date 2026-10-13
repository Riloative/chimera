/**
 * Per-conversation chat state and streaming. Lives outside components so a
 * response keeps streaming while the user navigates elsewhere in the app.
 */
import { create } from "zustand";
import { api, ApiError, stream, type SendBody } from "@/lib/api";
import type { Attachment, ConversationDetail, Message } from "@/lib/types";
import { useConversations } from "./conversations";
import { useModels } from "./models";

export interface ConvChat {
  messages: Message[];
  loaded: boolean;
  loadError: string | null;
  generationId: string | null;
  assistantId: string | null;
  streaming: boolean;
  /** Set when a request failed before the server accepted it. */
  sendError: string | null;
}

interface ChatState {
  convs: Record<string, ConvChat>;
  load: (id: string) => Promise<ConversationDetail | null>;
  send: (id: string, body: SendBody, optimisticAttachments?: Attachment[]) => Promise<void>;
  regenerate: (id: string, messageId?: string, target?: Partial<SendBody>) => Promise<void>;
  edit: (id: string, messageId: string, content: string, target?: Partial<SendBody>) => Promise<void>;
  stop: (id: string) => Promise<void>;
  deleteMessage: (id: string, messageId: string) => Promise<void>;
  clearSendError: (id: string) => void;
  forget: (id: string) => void;
}

const empty = (): ConvChat => ({
  messages: [],
  loaded: false,
  loadError: null,
  generationId: null,
  assistantId: null,
  streaming: false,
  sendError: null,
});

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
      if (idx === -1) return { messages: [...c.messages, msg].sort((a, b) => a.position - b.position) };
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
          // Drop anything at/after the new answer (regenerate/edit) and the optimistic user message.
          let messages = c.messages.filter((m) => !m.id.startsWith("temp-") && m.position < asst.position);
          if (data.user_message) {
            messages = messages.filter((m) => m.id !== data.user_message.id);
            messages.push(data.user_message);
          }
          messages.push(asst);
          messages.sort((a, b) => a.position - b.position);
          return { messages, generationId: data.generation_id, assistantId: asst.id, streaming: true, sendError: null };
        });
        useConversations.getState().upsert({ id: convId, updated_at: asst.created_at, generating: true });
        break;
      }
      case "snapshot":
        if (data.message) {
          upsertMessage(convId, data.message);
          patchConv(convId, () => ({ generationId: data.generation_id, assistantId: data.message.id, streaming: true }));
        }
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
    const message = err instanceof ApiError ? err.message : "Something went wrong.";
    patchConv(convId, (c) => ({
      messages: c.messages.filter((m) => !m.id.startsWith("temp-")),
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
        patchConv(id, () => ({ loaded: true, loadError: (err as Error).message }));
        return null;
      }
    },

    send: async (id, body, optimisticAttachments = []) => {
      if (get().convs[id]?.streaming) return;
      const now = new Date().toISOString();
      const lastPos = get().convs[id]?.messages.at(-1)?.position ?? -1;
      const temp: Message = {
        id: `temp-${Date.now()}`,
        conversation_id: id,
        position: lastPos + 1,
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
      patchConv(id, (c) => ({ messages: [...c.messages, temp], streaming: true, sendError: null, loaded: true }));
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

    deleteMessage: async (id, messageId) => {
      await api.deleteMessage(messageId);
      patchConv(id, (c) => ({ messages: c.messages.filter((m) => m.id !== messageId) }));
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
