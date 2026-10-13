import { useMemo } from "react";
import type { Conversation, ModelRef } from "./types";
import { firstAvailableModel, useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";

/**
 * The model a conversation (or the new-chat screen) uses, in priority order:
 * the conversation's own model, the last one picked, the configured default,
 * then the first model a reachable connection reported.
 */
export function useActiveModel(conv: Conversation | undefined): ModelRef | null {
  const selected = useUI((s) => s.selectedModel);
  const settings = useSettings((s) => s.settings);
  const entries = useModels((s) => s.entries);
  return useMemo(() => {
    if (conv?.provider_id && conv.model) return { providerId: conv.provider_id, model: conv.model };
    const exists = (r: ModelRef | null) => !!r && entries.some((e) => e.provider.id === r.providerId);
    if (exists(selected)) return selected;
    if (settings?.default_provider_id && settings.default_model) {
      const ref = { providerId: settings.default_provider_id, model: settings.default_model };
      if (exists(ref)) return ref;
    }
    return firstAvailableModel(entries);
  }, [conv?.provider_id, conv?.model, selected, settings?.default_provider_id, settings?.default_model, entries]);
}
