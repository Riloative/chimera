import { create } from "zustand";
import { api } from "@/lib/api";
import { errorText } from "@/lib/errors";
import type { AppSettings } from "@/lib/types";

const APPEARANCE_KEY = "chimera.appearance";
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

export function applyAppearance(s: Pick<AppSettings, "theme" | "font_size" | "chat_width" | "reduce_motion" | "sidebar_collapsed">) {
  const root = document.documentElement;
  const dark = s.theme === "dark" || (s.theme === "system" && !!media?.matches);
  root.classList.toggle("dark", dark);
  root.dataset.fontSize = s.font_size;
  root.dataset.chatWidth = s.chat_width;
  if (s.reduce_motion) root.dataset.reduceMotion = "true";
  else delete root.dataset.reduceMotion;
  try {
    localStorage.setItem(
      APPEARANCE_KEY,
      JSON.stringify({ theme: s.theme, font_size: s.font_size, chat_width: s.chat_width, reduce_motion: s.reduce_motion, sidebar_collapsed: s.sidebar_collapsed }),
    );
  } catch {
    /* storage unavailable */
  }
}

export function cachedAppearance(): Partial<AppSettings> {
  try {
    return JSON.parse(localStorage.getItem(APPEARANCE_KEY) || "{}");
  } catch {
    return {};
  }
}

interface SettingsState {
  settings: AppSettings | null;
  error: string | null;
  load: () => Promise<void>;
  update: (patch: Partial<AppSettings>) => Promise<AppSettings | null>;
}

export const useSettings = create<SettingsState>((set, get) => ({
  settings: null,
  error: null,
  load: async () => {
    try {
      const settings = await api.settings();
      applyAppearance(settings);
      set({ settings, error: null });
    } catch (err) {
      set({ error: errorText(err) });
    }
  },
  update: async (patch) => {
    const prev = get().settings;
    if (prev) {
      const optimistic = { ...prev, ...patch } as AppSettings;
      if (patch.params) optimistic.params = { ...prev.params, ...patch.params };
      if (patch.knowledge) optimistic.knowledge = { ...prev.knowledge, ...patch.knowledge };
      if (patch.web) optimistic.web = { ...prev.web, ...patch.web };
      set({ settings: optimistic });
      applyAppearance(optimistic);
    }
    try {
      const settings = await api.updateSettings(patch);
      set({ settings });
      applyAppearance(settings);
      return settings;
    } catch (err) {
      if (prev) {
        set({ settings: prev });
        applyAppearance(prev);
      }
      throw err;
    }
  },
}));

media?.addEventListener("change", () => {
  const s = useSettings.getState().settings;
  if (s?.theme === "system") applyAppearance(s);
});
