import * as Popover from "@radix-ui/react-popover";
import { AlertCircle, Check, ChevronDown, Eye, RefreshCw, Settings2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { providerErrorText } from "@/lib/errors";
import type { ModelRef } from "@/lib/types";
import { cn, compactNumber, modelLabel } from "@/lib/utils";
import { findProvider, useModels } from "@/stores/models";
import { Spinner, StatusDot } from "./ui";
import { ThinkingIndicator } from "./ThinkingIndicator";

export function ModelPicker({
  value,
  onChange,
  align = "end",
  side = "top",
  compact,
}: {
  value: ModelRef | null;
  onChange: (ref: ModelRef) => void;
  align?: "start" | "end";
  side?: "top" | "bottom";
  compact?: boolean;
}) {
  const { t } = useTranslation("models");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { entries, loading, refreshing, load } = useModels();
  const navigate = useNavigate();
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      // Discover fresh models each time the picker opens (cheap on a local server).
      void load(true);
    }
  }, [open, load]);

  const provider = findProvider(entries, value?.providerId);
  const totalModels = entries.reduce((n, e) => n + e.models.length, 0);
  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      entries.map((e) => ({
        ...e,
        models: q ? e.models.filter((m) => m.id.toLowerCase().includes(q) || (m.name ?? "").toLowerCase().includes(q)) : e.models,
      })),
    [entries, q],
  );
  const status = provider?.status;

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={value ? t("picker.current", { model: modelLabel(value.model) }) : t("picker.none")}
          className={cn(
            "flex h-8 max-w-[15rem] min-w-0 items-center gap-1.5 rounded-lg px-2.5 text-[0.8125rem] text-muted transition-colors hover:bg-hover hover:text-fg data-[state=open]:bg-hover data-[state=open]:text-fg",
            compact && "max-w-[11rem]",
          )}
        >
          {status === "error" && <AlertCircle className="size-3.5 shrink-0 text-danger" aria-label={t("picker.unreachable")} />}
          <span className="truncate" title={value?.model}>{value ? modelLabel(value.model) : t("picker.select")}</span>
          <ChevronDown className="size-3.5 shrink-0 opacity-70" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align={align}
          side={side}
          sideOffset={6}
          onOpenAutoFocus={(e) => {
            e.preventDefault();
            searchRef.current?.focus();
          }}
          className="animate-pop-in z-50 w-[min(24rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-line bg-elevated shadow-pop focus:outline-none"
        >
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("picker.search")}
              aria-label={t("picker.search")}
              className="h-7 min-w-0 flex-1 bg-transparent text-sm text-fg placeholder:text-faint focus:outline-none"
            />
            <button
              type="button"
              onClick={() => load(true)}
              aria-label={t("refreshModels")}
              title={t("refreshModels")}
              className="flex size-7 items-center justify-center rounded-md text-muted hover:bg-hover hover:text-fg"
            >
              {refreshing ? <ThinkingIndicator className="text-[0.875rem]" /> : <RefreshCw className="size-3.5" />}
            </button>
          </div>

          <div className="max-h-[min(24rem,60vh)] overflow-y-auto p-1" role="listbox" aria-label={t("title")}>
            {loading && !entries.length && (
              <div className="flex items-center gap-2 px-3 py-4 text-sm text-muted">
                <Spinner /> {t("picker.discovering")}
              </div>
            )}
            {!loading && entries.length === 0 && (
              <div className="px-3 py-5 text-center text-sm text-muted">
                {t("picker.noConnections")}
                <button type="button" className="mt-2 block w-full text-accent-text hover:underline" onClick={() => navigate("/models")}>
                  {t("picker.connect")}
                </button>
              </div>
            )}
            {filtered.map((entry) => (
              <div key={entry.provider.id} className="mb-1 last:mb-0">
                <div className="flex items-center justify-between gap-2 px-2.5 pt-2 pb-1">
                  <span className="truncate text-[0.72rem] font-medium text-faint">{entry.provider.name}</span>
                  <span className="text-[0.72rem] text-faint">
                    <StatusDot status={entry.provider.status} withLabel />
                  </span>
                </div>
                {entry.error && (
                  <p className="mx-2.5 mb-1 rounded-md bg-danger-soft px-2 py-1.5 text-[0.75rem] leading-snug text-danger">
                    {providerErrorText(entry.error_code, entry.error_params, entry.error)}
                    {entry.stale && ` ${t("picker.stale")}`}
                  </p>
                )}
                {!entry.provider.enabled && <p className="px-2.5 pb-1 text-[0.75rem] text-faint">{t("picker.disabled")}</p>}
                {entry.provider.enabled && !entry.error && entry.models.length === 0 && !q && (
                  <p className="px-2.5 pb-1 text-[0.75rem] text-faint">
                    {entry.provider.status === "unknown" ? t("picker.notTested") : t("picker.noModels")}
                  </p>
                )}
                {entry.models.map((m) => {
                  const selected = value?.providerId === entry.provider.id && value.model === m.id;
                  const ctx = m.context_length ?? (m.meta?.n_ctx as number | undefined);
                  return (
                    <button
                      key={m.id}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => {
                        onChange({ providerId: entry.provider.id, model: m.id });
                        setOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left transition-colors hover:bg-hover",
                        selected && "bg-hover",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-fg" title={m.id}>
                          {m.name || modelLabel(m.id)}
                        </span>
                        <span className="flex items-center gap-2 text-[0.72rem] text-faint">
                          {ctx ? <span>{t("ctx", { value: compactNumber(ctx) })}</span> : null}
                          {m.vision && (
                            <span className="inline-flex items-center gap-0.5">
                              <Eye className="size-3" /> {t("vision")}
                            </span>
                          )}
                          {m.source === "manual" && <span>{t("manual")}</span>}
                          {m.loaded === false && <span>{t("notLoaded")}</span>}
                        </span>
                      </span>
                      {selected && <Check className="size-4 shrink-0 text-accent" />}
                    </button>
                  );
                })}
              </div>
            ))}
            {q && totalModels > 0 && filtered.every((e) => e.models.length === 0) && (
              <p className="px-3 py-4 text-center text-sm text-muted">{t("picker.noMatch", { query })}</p>
            )}
          </div>

          <div className="border-t border-line p-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/models");
              }}
              className="flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-[0.8125rem] text-muted hover:bg-hover hover:text-fg"
            >
              <Settings2 className="size-3.5" /> {t("picker.manage")}
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
