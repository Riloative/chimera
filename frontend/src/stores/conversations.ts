import { create } from "zustand";
import { api } from "@/lib/api";
import { errorText } from "@/lib/errors";
import type { Conversation } from "@/lib/types";

interface ConversationsState {
  items: Conversation[];
  loaded: boolean;
  error: string | null;
  /** The server may have older conversations than the ones loaded. */
  hasMore: boolean;
  loadingMore: boolean;
  load: () => Promise<void>;
  loadMore: () => Promise<void>;
  upsert: (c: Partial<Conversation> & { id: string }) => void;
  patch: (id: string, data: Partial<Conversation>) => Promise<Conversation>;
  /** Resolves once every change started for this conversation has been saved (or failed). */
  settled: (id: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
}

/** The server's page size (GET /api/conversations). */
export const PAGE_SIZE = 500;

/** Changes made while a list request is in flight (e.g. a live title, a new
 * chat): applied on top of the response so a slower, older list never undoes
 * them. */
let pendingChanges: Map<string, Partial<Conversation>> | null = null;

/** Saves in flight per conversation, so a message sent right after changing a
 * setting (thinking, web search) is only sent once the server has it. */
const inFlight = new Map<string, Set<Promise<unknown>>>();

export const useConversations = create<ConversationsState>((set, get) => ({
  items: [],
  loaded: false,
  error: null,
  hasMore: false,
  loadingMore: false,
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
      set({ items: [...created, ...items], loaded: true, error: null, hasMore: fetched.length >= PAGE_SIZE });
    } catch (err) {
      set({ error: errorText(err), loaded: true });
    } finally {
      if (pendingChanges === changes) pendingChanges = null;
    }
  },
  loadMore: async () => {
    if (get().loadingMore || !get().hasMore) return;
    set({ loadingMore: true });
    try {
      const page = await api.conversations(get().items.length);
      set((s) => {
        const known = new Set(s.items.map((c) => c.id));
        return { items: [...s.items, ...page.filter((c) => !known.has(c.id))], hasMore: page.length >= PAGE_SIZE };
      });
    } catch (err) {
      set({ error: errorText(err) });
    } finally {
      set({ loadingMore: false });
    }
  },
  upsert: (c) =>
    set((s) => {
      if (pendingChanges) pendingChanges.set(c.id, { ...pendingChanges.get(c.id), ...c });
      const idx = s.items.findIndex((i) => i.id === c.id);
      if (idx === -1) {
        // Only a complete record becomes a row: a partial update (a title, "generating") of a
        // conversation not in the list would otherwise show as an empty entry.
        if (typeof c.title !== "string" || typeof c.created_at !== "string") return s;
        return { items: [c as Conversation, ...s.items] };
      }
      const items = s.items.slice();
      items[idx] = { ...items[idx], ...c };
      return { items };
    }),
  patch: async (id, data) => {
    const prev = get().items.find((i) => i.id === id);
    if (prev) get().upsert({ ...prev, ...data });
    const request = api.updateConversation(id, data as Record<string, unknown>);
    const set = inFlight.get(id) ?? new Set();
    inFlight.set(id, set.add(request));
    try {
      const updated = await request;
      get().upsert(updated);
      return updated;
    } catch (err) {
      if (prev) get().upsert(prev);
      throw err;
    } finally {
      set.delete(request);
      if (!set.size) inFlight.delete(id);
    }
  },
  settled: async (id) => {
    await Promise.allSettled([...(inFlight.get(id) ?? [])]);
  },
  remove: async (id) => {
    await api.deleteConversation(id);
    pendingChanges?.delete(id);
    set((s) => ({ items: s.items.filter((i) => i.id !== id) }));
  },
}));
