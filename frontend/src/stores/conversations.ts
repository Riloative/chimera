import { create } from "zustand";
import { api } from "@/lib/api";
import { errorText } from "@/lib/errors";
import type { Conversation } from "@/lib/types";

interface ConversationsState {
  items: Conversation[];
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;
  upsert: (c: Partial<Conversation> & { id: string }) => void;
  patch: (id: string, data: Partial<Conversation>) => Promise<Conversation>;
  remove: (id: string) => Promise<void>;
}

/** Changes made while a list request is in flight (e.g. a live title, a new
 * chat): applied on top of the response so a slower, older list never undoes
 * them. */
let pendingChanges: Map<string, Partial<Conversation>> | null = null;

export const useConversations = create<ConversationsState>((set, get) => ({
  items: [],
  loaded: false,
  error: null,
  load: async () => {
    const changes = new Map<string, Partial<Conversation>>();
    pendingChanges = changes;
    try {
      const fetched = await api.conversations();
      const known = new Set(fetched.map((c) => c.id));
      const items = fetched.map((c) => (changes.has(c.id) ? { ...c, ...changes.get(c.id) } : c));
      // Created meanwhile (only complete records: a partial change of an unknown id is dropped).
      const created = [...changes.entries()]
        .filter(([id, c]) => !known.has(id) && typeof c.title === "string" && typeof c.created_at === "string")
        .map(([, c]) => c as Conversation);
      set({ items: [...created, ...items], loaded: true, error: null });
    } catch (err) {
      set({ error: errorText(err), loaded: true });
    } finally {
      if (pendingChanges === changes) pendingChanges = null;
    }
  },
  upsert: (c) =>
    set((s) => {
      if (pendingChanges) pendingChanges.set(c.id, { ...pendingChanges.get(c.id), ...c });
      const idx = s.items.findIndex((i) => i.id === c.id);
      if (idx === -1) return { items: [c as Conversation, ...s.items] };
      const items = s.items.slice();
      items[idx] = { ...items[idx], ...c };
      return { items };
    }),
  patch: async (id, data) => {
    const prev = get().items.find((i) => i.id === id);
    if (prev) get().upsert({ ...prev, ...data });
    try {
      const updated = await api.updateConversation(id, data as Record<string, unknown>);
      get().upsert(updated);
      return updated;
    } catch (err) {
      if (prev) get().upsert(prev);
      throw err;
    }
  },
  remove: async (id) => {
    await api.deleteConversation(id);
    pendingChanges?.delete(id);
    set((s) => ({ items: s.items.filter((i) => i.id !== id) }));
  },
}));
