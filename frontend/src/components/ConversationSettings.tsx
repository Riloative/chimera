import { Library } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import type { Collection, GenerationParams, Prompt } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useSettings } from "@/stores/settings";
import { cleanParams, ParamsEditor } from "./ParamsEditor";
import { Button, Dialog, SegmentedControl, Spinner, Textarea } from "./ui";

export interface ConversationSettingsValue {
  systemPrompt: string | null;
  params: GenerationParams;
  collectionIds: string[];
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
  const [tab, setTab] = useState<Tab>(initialTab);
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
    api.collections().then(setCollections).catch(() => setCollections([]));
    api.prompts().then(setPrompts).catch(() => setPrompts([]));
  }, [open, value, initialTab]);

  const save = async () => {
    setSaving(true);
    try {
      await onSave({ systemPrompt: useDefault ? null : systemPrompt, params: cleanParams(params), collectionIds });
      onOpenChange(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Conversation settings"
      description="These apply to this conversation only. Empty fields use your defaults from Settings."
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} loading={saving}>
            Save
          </Button>
        </>
      }
    >
      <div className="mb-4">
        <SegmentedControl<Tab>
          label="Section"
          value={tab}
          onChange={setTab}
          options={[
            { value: "prompt", label: "System prompt" },
            { value: "params", label: "Parameters" },
            { value: "knowledge", label: collectionIds.length ? `Knowledge (${collectionIds.length})` : "Knowledge" },
          ]}
        />
      </div>

      {tab === "prompt" && (
        <div className="space-y-3 pb-2">
          <label className="flex items-center gap-2 text-sm text-fg">
            <input type="checkbox" checked={useDefault} onChange={(e) => setUseDefault(e.target.checked)} className="accent-[var(--accent)]" />
            Use the default system prompt
          </label>
          {useDefault ? (
            <p className="rounded-lg bg-subtle px-3 py-2.5 text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-muted">
              {settings?.default_system_prompt || "No default system prompt is set."}
            </p>
          ) : (
            <>
              <Textarea
                rows={8}
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                placeholder="You are a helpful assistant…"
                aria-label="System prompt"
                autoFocus
              />
              {prompts.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[0.75rem] text-faint">From library:</span>
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

      {tab === "params" && <ParamsEditor value={params} onChange={setParams} supported={supportedParams} inherited={settings?.params} />}

      {tab === "knowledge" && (
        <div className="pb-2">
          <p className="mb-3 text-[0.8125rem] leading-relaxed text-muted">
            Relevant excerpts from the selected collections are retrieved locally for each message and given to the model, with sources shown under the answer.
          </p>
          {collections === null ? (
            <Spinner />
          ) : collections.length === 0 ? (
            <div className="rounded-xl border border-dashed border-line-strong px-4 py-6 text-center text-sm text-muted">
              <Library className="mx-auto mb-2 size-5 text-faint" />
              No collections yet.{" "}
              <Link to="/knowledge" className="text-accent-text hover:underline" onClick={() => onOpenChange(false)}>
                Create one
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
                          {c.document_count} document{c.document_count === 1 ? "" : "s"} · {c.chunk_count} chunks
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
