import { create } from "zustand";
import { api } from "@/lib/api";
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

export const useConversations = create<ConversationsState>((set, get) => ({
  items: [],
  loaded: false,
  error: null,
  load: async () => {
    try {
      const items = await api.conversations();
      set({ items, loaded: true, error: null });
    } catch (err) {
      set({ error: (err as Error).message, loaded: true });
    }
  },
  upsert: (c) =>
    set((s) => {
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
    set((s) => ({ items: s.items.filter((i) => i.id !== id) }));
  },
}));
