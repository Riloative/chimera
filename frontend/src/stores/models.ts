import { create } from "zustand";
import { api } from "@/lib/api";
import type { ModelInfo, ModelRef, Provider, ProviderModels } from "@/lib/types";

interface ModelsState {
  entries: ProviderModels[];
  loaded: boolean;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  load: (refresh?: boolean) => Promise<void>;
  setProvider: (provider: Provider) => void;
  removeProvider: (id: string) => void;
}

export const useModels = create<ModelsState>((set, get) => ({
  entries: [],
  loaded: false,
  loading: false,
  refreshing: false,
  error: null,
  load: async (refresh = false) => {
    if (refresh) set({ refreshing: true });
    else set({ loading: !get().loaded });
    try {
      const { providers } = await api.models(refresh);
      set({ entries: providers, loaded: true, error: null });
    } catch (err) {
      set({ error: (err as Error).message, loaded: true });
    } finally {
      set({ loading: false, refreshing: false });
    }
  },
  setProvider: (provider) =>
    set((s) => {
      const exists = s.entries.some((e) => e.provider.id === provider.id);
      return {
        entries: exists
          ? s.entries.map((e) => (e.provider.id === provider.id ? { ...e, provider } : e))
          : [...s.entries, { provider, models: [], stale: false, error: null }],
      };
    }),
  removeProvider: (id) => set((s) => ({ entries: s.entries.filter((e) => e.provider.id !== id) })),
}));

export function findProvider(entries: ProviderModels[], id: string | null | undefined): Provider | undefined {
  return entries.find((e) => e.provider.id === id)?.provider;
}

export function findModel(entries: ProviderModels[], ref: ModelRef | null): ModelInfo | undefined {
  if (!ref) return undefined;
  return entries.find((e) => e.provider.id === ref.providerId)?.models.find((m) => m.id === ref.model);
}

/** True only when the server (or the user's explicit override) says so. */
export function modelSupportsVision(entries: ProviderModels[], ref: ModelRef | null): boolean {
  if (!ref) return false;
  const provider = findProvider(entries, ref.providerId);
  if (!provider) return false;
  if (provider.vision === "on") return true;
  if (provider.vision === "off") return false;
  const model = findModel(entries, ref);
  if (model?.vision != null) return model.vision;
  return !!provider.capabilities?.vision;
}

export function firstAvailableModel(entries: ProviderModels[]): ModelRef | null {
  for (const e of entries) {
    if (!e.provider.enabled || e.provider.status === "error") continue;
    if (e.models[0]) return { providerId: e.provider.id, model: e.models[0].id };
  }
  return null;
}
