/** Instance-wide web status and per-conversation "search the web" state. */
import { create } from "zustand";
import { api } from "@/lib/api";
import type { WebStatus } from "@/lib/types";

interface WebStore {
  status: WebStatus | null;
  /** Search toggle per conversation key ("new" for the start page). */
  search: Record<string, boolean>;
  load: () => Promise<void>;
  setSearch: (key: string, on: boolean) => void;
  moveSearch: (from: string, to: string) => void;
}

export const useWeb = create<WebStore>((set, get) => ({
  status: null,
  search: {},
  load: async () => {
    try {
      set({ status: await api.webStatus() });
    } catch {
      set({ status: null }); // treated as "web access unavailable"
    }
  },
  setSearch: (key, on) => set((s) => ({ search: { ...s.search, [key]: on } })),
  moveSearch: (from, to) => {
    const value = get().search[from];
    if (value === undefined) return;
    set((s) => {
      const search = { ...s.search, [to]: value };
      delete search[from];
      return { search };
    });
  },
}));

/** The browser's IANA time zone (sent so answers know the local date). */
export function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}
