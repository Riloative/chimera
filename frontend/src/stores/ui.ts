import { create } from "zustand";
import type { GenerationParams, ModelRef, ThinkingLevel } from "@/lib/types";

export interface Toast {
  id: number;
  message: string;
  tone?: "default" | "error" | "success";
  action?: { label: string; onClick: () => void };
}

/** Settings chosen on the empty "new chat" screen, applied when it is created. */
export interface Draft {
  systemPrompt: string | null;
  params: GenerationParams;
  collectionIds: string[];
  contextStrategy?: "sliding" | "summarize" | "off" | null;
  /** Thinking chosen on the start page; undefined = the user's default. */
  thinking?: ThinkingLevel;
}

interface UIState {
  mobileSidebarOpen: boolean;
  searchOpen: boolean;
  toasts: Toast[];
  composerInsert: string | null;
  selectedModel: ModelRef | null;
  draft: Draft;
  /** Length of the composer text and the conversation it belongs to (the context indicator's draft estimate). */
  draftChars: { key: string; n: number };
  setDraftChars: (key: string, n: number) => void;
  /** Unsent composer text per conversation ("new" = the start page), so a draft never follows you into another chat. */
  composerDrafts: Record<string, string>;
  setComposerDraft: (key: string, text: string) => void;
  setMobileSidebar: (open: boolean) => void;
  setSearchOpen: (open: boolean) => void;
  toast: (t: Omit<Toast, "id">) => void;
  dismissToast: (id: number) => void;
  insertIntoComposer: (text: string | null) => void;
  setSelectedModel: (ref: ModelRef | null) => void;
  setDraft: (d: Partial<Draft>) => void;
  resetDraft: () => void;
}

const LAST_MODEL_KEY = "chimera.lastModel";
const emptyDraft: Draft = { systemPrompt: null, params: {}, collectionIds: [] };

function readLastModel(): ModelRef | null {
  try {
    const v = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) || "null");
    return v && typeof v.providerId === "string" && typeof v.model === "string" ? v : null;
  } catch {
    return null;
  }
}

let toastId = 0;

export const useUI = create<UIState>((set) => ({
  mobileSidebarOpen: false,
  searchOpen: false,
  toasts: [],
  composerInsert: null,
  selectedModel: readLastModel(),
  draft: emptyDraft,
  setMobileSidebar: (open) => set({ mobileSidebarOpen: open }),
  setSearchOpen: (open) => set({ searchOpen: open }),
  toast: (t) => {
    const id = ++toastId;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { ...t, id }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })), t.tone === "error" ? 7000 : 3500);
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
  insertIntoComposer: (text) => set({ composerInsert: text }),
  setSelectedModel: (ref) => {
    try {
      localStorage.setItem(LAST_MODEL_KEY, JSON.stringify(ref));
    } catch {
      /* ignore */
    }
    set({ selectedModel: ref });
  },
  setDraft: (d) => set((s) => ({ draft: { ...s.draft, ...d } })),
  resetDraft: () => set({ draft: emptyDraft }),
  draftChars: { key: "", n: 0 },
  setDraftChars: (key, n) => set((s) => (s.draftChars.key === key && s.draftChars.n === n ? s : { draftChars: { key, n } })),
  composerDrafts: {},
  setComposerDraft: (key, text) =>
    set((s) => {
      if ((s.composerDrafts[key] ?? "") === text) return s;
      const next = { ...s.composerDrafts };
      if (text) next[key] = text;
      else delete next[key];
      return { composerDrafts: next };
    }),
}));

export const toast = (message: string, tone: Toast["tone"] = "default", action?: Toast["action"]) =>
  useUI.getState().toast({ message, tone, action });
