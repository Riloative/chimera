import { Library } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import type { Collection, GenerationParams, Prompt } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSettings } from "@/stores/settings";
import { cleanParams, ParamsEditor } from "./ParamsEditor";
import { Button, Dialog, SegmentedControl, Spinner, Textarea, Field, Select } from "./ui";

export type ContextStrategy = "sliding" | "summarize" | "off";

export interface ConversationSettingsValue {
  systemPrompt: string | null;
  params: GenerationParams;
  collectionIds: string[];
  /** null = the user's default strategy. */
  contextStrategy?: ContextStrategy | null;
}

type Tab = "prompt" | "params" | "knowledge";

export function ConversationSettings({
  open,
  onOpenChange,
  value,
  onSave,
  supportedParams,
  initialTab = "prompt",
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  value: ConversationSettingsValue;
  onSave: (v: ConversationSettingsValue) => Promise<void> | void;
  supportedParams?: string[];
  initialTab?: Tab;
}) {
  const { t } = useTranslation(["chat", "common", "knowledge", "context"]);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [strategy, setStrategy] = useState<ContextStrategy | "">("");
  const [systemPrompt, setSystemPrompt] = useState<string>("");
  const [useDefault, setUseDefault] = useState(true);
  const [params, setParams] = useState<GenerationParams>({});
  const [collectionIds, setCollectionIds] = useState<string[]>([]);
  const [collections, setCollections] = useState<Collection[] | null>(null);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [saving, setSaving] = useState(false);
  const settings = useSettings((s) => s.settings);

  useEffect(() => {
    if (!open) return;
    setTab(initialTab);
    setUseDefault(value.systemPrompt == null);
    setSystemPrompt(value.systemPrompt ?? "");
    setParams(value.params ?? {});
    setCollectionIds(value.collectionIds ?? []);
    setStrategy(value.contextStrategy ?? "");
    api.collections().then(setCollections).catch(() => setCollections([]));
    api.prompts().then(setPrompts).catch(() => setPrompts([]));
  }, [open, value, initialTab]);

  const save = async () => {
    setSaving(true);
    try {
      await onSave({ systemPrompt: useDefault ? null : systemPrompt, params: cleanParams(params), collectionIds, contextStrategy: strategy || null });
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("settings.title")}
      description={t("settings.description")}
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button variant="primary" onClick={save} loading={saving}>
            {t("common:save")}
          </Button>
        </>
      }
    >
      <div className="mb-4">
        <SegmentedControl<Tab>
          label={t("settings.section")}
          value={tab}
          onChange={setTab}
          options={[
            { value: "prompt", label: t("settings.systemPrompt") },
            { value: "params", label: t("settings.parameters") },
            { value: "knowledge", label: collectionIds.length ? t("settings.knowledgeCount", { count: collectionIds.length }) : t("settings.knowledge") },
          ]}
        />
      </div>

      {tab === "prompt" && (
        <div className="space-y-3 pb-2">
          <label className="flex items-center gap-2 text-sm text-fg">
            <input type="checkbox" checked={useDefault} onChange={(e) => setUseDefault(e.target.checked)} className="accent-[var(--accent)]" />
            {t("settings.useDefault")}
          </label>
          {useDefault ? (
            <p className="rounded-lg bg-subtle px-3 py-2.5 text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-muted">
              {settings?.default_system_prompt || t("settings.noDefault")}
            </p>
          ) : (
            <>
              <Textarea
                rows={8}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder={t("settings.promptPlaceholder")}
                aria-label={t("settings.systemPrompt")}
                autoFocus
              />
              {prompts.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[0.75rem] text-faint">{t("settings.fromLibrary")}</span>
                  {prompts.slice(0, 8).map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setSystemPrompt(p.content)}
                      className="h-6 rounded-md border border-line px-2 text-[0.75rem] text-muted hover:bg-hover hover:text-fg"
                    >
                      {p.title}
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {tab === "params" && (
        <div className="space-y-5 pb-2">
          <ParamsEditor value={params} onChange={setParams} supported={supportedParams} inherited={settings?.params} />
          <Field label={t("context:strategy.label")} htmlFor="cs-strategy" hint={t("context:strategy.help")}>
            <Select id="cs-strategy" value={strategy} onChange={(e) => setStrategy(e.target.value as ContextStrategy | "")}>
              <option value="">{t("context:strategy.inherit", { name: t(`context:strategy.${settings?.context?.strategy ?? "sliding"}`) })}</option>
              <option value="sliding">{t("context:strategy.sliding")}</option>
              <option value="summarize">{t("context:strategy.summarize")}</option>
              <option value="off">{t("context:strategy.off")}</option>
            </Select>
          </Field>
          {strategy && <p className="-mt-3 text-xs leading-relaxed text-muted">{t(`context:strategy.${strategy}Help`)}</p>}
        </div>
      )}

      {tab === "knowledge" && (
        <div className="pb-2">
          <p className="mb-3 text-[0.8125rem] leading-relaxed text-muted">
            {t("settings.knowledgeHelp")}
          </p>
          {collections === null ? (
            <Spinner />
          ) : collections.length === 0 ? (
            <div className="rounded-xl border border-dashed border-line-strong px-4 py-6 text-center text-sm text-muted">
              <Library className="mx-auto mb-2 size-5 text-faint" />
              {t("settings.noCollections")}{" "}
              <Link to="/knowledge" className="text-accent-text hover:underline" onClick={() => onOpenChange(false)}>
                {t("settings.createCollection")}
              </Link>
            </div>
          ) : (
            <ul className="space-y-1">
              {collections.map((c) => {
                const checked = collectionIds.includes(c.id);
                return (
                  <li key={c.id}>
                    <label
                      className={cn(
                        "flex cursor-pointer items-center gap-3 rounded-lg border px-3 py-2.5 transition-colors",
                        checked ? "border-accent/50 bg-accent-soft" : "border-line hover:bg-hover",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => setCollectionIds((ids) => (e.target.checked ? [...ids, c.id] : ids.filter((i) => i !== c.id)))}
                        className="accent-[var(--accent)]"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-fg">{c.name}</span>
                        <span className="block text-[0.75rem] text-faint">
                          {t("knowledge:documents", { count: c.document_count })} · {t("knowledge:chunks", { count: c.chunk_count })}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </Dialog>
  );
}
