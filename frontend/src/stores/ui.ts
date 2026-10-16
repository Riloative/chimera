import { create } from "zustand";
import type { GenerationParams, ModelRef } from "@/lib/types";

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
}

interface UIState {
  mobileSidebarOpen: boolean;
  searchOpen: boolean;
  toasts: Toast[];
  composerInsert: string | null;
  selectedModel: ModelRef | null;
  draft: Draft;
  /** Length of the composer text (for the context indicator's draft estimate). */
  draftChars: number;
  setDraftChars: (n: number) => void;
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
  draftChars: 0,
  setDraftChars: (n) => set((s) => (s.draftChars === n ? s : { draftChars: n })),
}));

export const toast = (message: string, tone: Toast["tone"] = "default", action?: Toast["action"]) =>
  useUI.getState().toast({ message, tone, action });
