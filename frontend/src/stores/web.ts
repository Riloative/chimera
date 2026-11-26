/** Instance-wide web status, the start page's search mode, and one-off overrides. */
import { create } from "zustand";
import { api } from "@/lib/api";
import type { SearchMode, WebStatus } from "@/lib/types";

interface WebStore {
  status: WebStatus | null;
  /** Search mode chosen on the start page before the conversation exists (null = the user's default). */
  draftMode: SearchMode | null;
  /** One-off choice for the next message, by conversation key ("new" for the start page). */
  override: Record<string, boolean>;
  load: () => Promise<void>;
  setDraftMode: (mode: SearchMode | null) => void;
  setOverride: (key: string, value: boolean | null) => void;
  moveOverride: (from: string, to: string) => void;
}

export const useWeb = create<WebStore>((set, get) => ({
  status: null,
  draftMode: null,
  override: {},
  load: async () => {
    try {
      set({ status: await api.webStatus() });
    } catch {
      set({ status: null }); // treated as "web access unavailable"
    }
  },
  setDraftMode: (mode) => set({ draftMode: mode }),
  setOverride: (key, value) =>
    set((s) => {
      const override = { ...s.override };
      if (value === null) delete override[key];
      else override[key] = value;
      return { override };
    }),
  moveOverride: (from, to) => {
    const value = get().override[from];
    if (value === undefined) return;
    get().setOverride(from, null);
    get().setOverride(to, value);
  },
}));

/** Whether a message searches: the one-off choice wins, then the conversation's mode. */
export function effectiveSearch(mode: SearchMode, override: boolean | undefined): SearchMode {
  if (override === true) return "on";
  if (override === false) return "off";
  return mode;
}

/** The browser's IANA time zone (sent so answers know the local date). */
export function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}
