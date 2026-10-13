import { Download, HardDrive, Monitor, Moon, Sun, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { PageHeader, PageShell } from "@/components/Page";
import { ParamsEditor, cleanParams } from "@/components/ParamsEditor";
import { Button, ConfirmDialog, Field, Input, SegmentedControl, Select, Spinner, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { AppSettings, GenerationParams } from "@/lib/types";
import { cn, downloadUrl, formatBytes } from "@/lib/utils";
import { useConversations } from "@/stores/conversations";
import { findProvider, useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";

const SECTIONS = [
  { id: "appearance", label: "Appearance" },
  { id: "chat", label: "Chat" },
  { id: "documents", label: "Documents" },
  { id: "data", label: "Privacy & data" },
  { id: "about", label: "About" },
] as const;

export function SettingsPage() {
  const { section = "appearance" } = useParams();
  const settings = useSettings((s) => s.settings);

  return (
    <PageShell wide>
      <PageHeader title="Settings" />
      <div className="flex flex-col gap-8 md:flex-row md:gap-12">
        <nav aria-label="Settings sections" className="flex shrink-0 gap-1 overflow-x-auto md:w-44 md:flex-col">
          {SECTIONS.map((s) => (
            <NavLink
              key={s.id}
              to={`/settings/${s.id}`}
              className={({ isActive }) =>
                cn(
                  "flex h-8 shrink-0 items-center rounded-lg px-3 text-[0.8125rem] transition-colors",
                  isActive || (section === s.id) ? "bg-active text-fg" : "text-muted hover:bg-hover hover:text-fg",
                )
              }
            >
              {s.label}
            </NavLink>
          ))}
          <Link to="/models" className="flex h-8 shrink-0 items-center rounded-lg px-3 text-[0.8125rem] text-muted transition-colors hover:bg-hover hover:text-fg">
            Models & connections →
          </Link>
        </nav>
        <div className="min-w-0 flex-1 md:max-w-2xl">
          {!settings ? (
            <Spinner />
          ) : section === "appearance" ? (
            <Appearance settings={settings} />
          ) : section === "chat" ? (
            <ChatSettings settings={settings} />
          ) : section === "documents" ? (
            <DocumentSettings settings={settings} />
          ) : section === "data" ? (
            <DataSettings />
          ) : (
            <About />
          )}
        </div>
      </div>
    </PageShell>
  );
}

function Group({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-10 last:mb-0">
      <h2 className="text-[0.9375rem] font-medium text-fg">{title}</h2>
      {description && <p className="mt-0.5 text-[0.8125rem] leading-relaxed text-muted">{description}</p>}
      <div className="mt-4 space-y-5">{children}</div>
    </section>
  );
}

function useSave() {
  const update = useSettings((s) => s.update);
  return (patch: Partial<AppSettings>) => update(patch).catch((e) => toast((e as Error).message, "error"));
}

function Row({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="text-sm font-medium text-fg">{label}</p>
        {description && <p className="text-[0.8125rem] text-muted">{description}</p>}
      </div>
      {children}
    </div>
  );
}

function Appearance({ settings }: { settings: AppSettings }) {
  const save = useSave();
  return (
    <>
      <Group title="Theme">
        <SegmentedControl
          label="Theme"
          value={settings.theme}
          onChange={(theme) => save({ theme })}
          options={[
            { value: "system", label: <><Monitor className="size-3.5" /> System</> },
            { value: "light", label: <><Sun className="size-3.5" /> Light</> },
            { value: "dark", label: <><Moon className="size-3.5" /> Dark</> },
          ]}
        />
      </Group>
      <Group title="Reading">
        <Row label="Font size">
          <SegmentedControl
            label="Font size"
            value={settings.font_size}
            onChange={(font_size) => save({ font_size })}
            options={[
              { value: "sm", label: "Small" },
              { value: "md", label: "Default" },
              { value: "lg", label: "Large" },
            ]}
          />
        </Row>
        <Row label="Conversation width">
          <SegmentedControl
            label="Conversation width"
            value={settings.chat_width}
            onChange={(chat_width) => save({ chat_width })}
            options={[
              { value: "narrow", label: "Narrow" },
              { value: "default", label: "Default" },
              { value: "wide", label: "Wide" },
            ]}
          />
        </Row>
      </Group>
      <Group title="Interface">
        <Switch label="Collapse sidebar" description="Show a slim icon rail instead of the full sidebar." checked={settings.sidebar_collapsed} onChange={(v) => save({ sidebar_collapsed: v })} />
        <Switch
          label="Reduce motion"
          description="Turn off animations and transitions. Your system preference is always respected."
          checked={settings.reduce_motion}
          onChange={(v) => save({ reduce_motion: v })}
        />
      </Group>
    </>
  );
}

function ChatSettings({ settings }: { settings: AppSettings }) {
  const save = useSave();
  const entries = useModels((s) => s.entries);
  const [prompt, setPrompt] = useState(settings.default_system_prompt);
  const [params, setParams] = useState<GenerationParams>(settings.params);
  const [dirty, setDirty] = useState(false);
  const provider = findProvider(entries, settings.default_provider_id);

  useEffect(() => {
    setPrompt(settings.default_system_prompt);
    setParams(settings.params);
  }, [settings.default_system_prompt, settings.params]);

  return (
    <>
      <Group title="Behavior">
        <Switch label="Enter to send" description="When off, Enter adds a new line and Ctrl/⌘+Enter sends." checked={settings.enter_to_send} onChange={(v) => save({ enter_to_send: v })} />
        <Switch label="Stream responses" description="Show text as it is generated. When off, the full answer appears at once." checked={settings.stream} onChange={(v) => save({ stream: v })} />
        <Switch
          label="Generate titles with the model"
          description="After the first reply, ask the model for a short title. Otherwise the first message is used."
          checked={settings.auto_title}
          onChange={(v) => save({ auto_title: v })}
        />
        <Switch label="Show reasoning" description="Display the thought process of reasoning models in a collapsible section." checked={settings.show_reasoning} onChange={(v) => save({ show_reasoning: v })} />
      </Group>
      <Group title="Default system prompt" description="Used by every conversation unless it sets its own.">
        <Textarea
          rows={6}
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setDirty(true);
          }}
          placeholder="You are a concise, helpful assistant."
          aria-label="Default system prompt"
        />
      </Group>
      <Group
        title="Default generation parameters"
        description={
          <>
            Leave a field empty to use the server's own default. Parameters are only sent when the connection supports them
            {provider ? <> (checked against <span className="text-fg">{provider.name}</span>)</> : null}.
          </>
        }
      >
        <ParamsEditor
          value={params}
          onChange={(p) => {
            setParams(p);
            setDirty(true);
          }}
          supported={provider?.supported_params}
        />
      </Group>
      <div className="sticky bottom-0 -mx-1 flex justify-end gap-2 bg-bg px-1 py-3">
        <Button
          variant="primary"
          disabled={!dirty}
          onClick={async () => {
            const cleaned = cleanParams(params);
            const full: GenerationParams = {
              temperature: null, top_p: null, top_k: null, min_p: null, max_tokens: null, stop: null,
              repeat_penalty: null, presence_penalty: null, frequency_penalty: null, seed: null, ...cleaned,
            };
            await save({ default_system_prompt: prompt, params: full });
            setDirty(false);
            toast("Chat settings saved", "success");
          }}
        >
          Save changes
        </Button>
      </div>
    </>
  );
}

function DocumentSettings({ settings }: { settings: AppSettings }) {
  const save = useSave();
  const entries = useModels((s) => s.entries);
  const k = settings.knowledge;
  const [mb, setMb] = useState(settings.max_attachment_mb);
  const [chars, setChars] = useState(settings.max_attachment_chars);
  const [embedModel, setEmbedModel] = useState(k.embedding_model ?? "");
  const [chunk, setChunk] = useState({ chunk_size: k.chunk_size, chunk_overlap: k.chunk_overlap, top_k: k.top_k });

  return (
    <>
      <Group title="Attachments" description="Files are read on this server; extracted text is sent to the model with your message.">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Maximum file size (MB)" htmlFor="att-mb">
            <Input id="att-mb" type="number" min={1} max={100} value={mb} onChange={(e) => setMb(Number(e.target.value))} onBlur={() => mb !== settings.max_attachment_mb && save({ max_attachment_mb: Math.min(100, Math.max(1, mb || 10)) })} />
          </Field>
          <Field label="Maximum extracted characters" htmlFor="att-chars" hint="Longer documents are truncated; use Knowledge for large files.">
            <Input
              id="att-chars"
              type="number"
              min={1000}
              max={2000000}
              step={1000}
              value={chars}
              onChange={(e) => setChars(Number(e.target.value))}
              onBlur={() => chars !== settings.max_attachment_chars && save({ max_attachment_chars: Math.min(2_000_000, Math.max(1000, chars || 100000)) })}
            />
          </Field>
        </div>
      </Group>
      <Group
        title="Knowledge base"
        description="Keyword search works offline out of the box. Add a local embedding model for semantic search (e.g. llama-server started with --embeddings, or Ollama's nomic-embed-text)."
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Embedding connection" htmlFor="kb-provider">
            <Select
              id="kb-provider"
              value={k.embedding_provider_id ?? ""}
              onChange={(e) => save({ knowledge: { ...k, embedding_provider_id: e.target.value || null, embedding_model: e.target.value ? k.embedding_model : null } })}
            >
              <option value="">None (keyword search only)</option>
              {entries.map((e) => (
                <option key={e.provider.id} value={e.provider.id}>
                  {e.provider.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Embedding model" htmlFor="kb-model" hint="Re-index existing documents after changing it.">
            <Input
              id="kb-model"
              list="kb-models"
              value={embedModel}
              disabled={!k.embedding_provider_id}
              onChange={(e) => setEmbedModel(e.target.value)}
              onBlur={() => embedModel !== (k.embedding_model ?? "") && save({ knowledge: { ...k, embedding_model: embedModel.trim() || null } })}
              className="font-mono"
              placeholder="nomic-embed-text"
            />
            <datalist id="kb-models">
              {entries.find((e) => e.provider.id === k.embedding_provider_id)?.models.map((m) => <option key={m.id} value={m.id} />)}
            </datalist>
          </Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {(
            [
              ["chunk_size", "Chunk size (chars)", 200, 8000],
              ["chunk_overlap", "Overlap (chars)", 0, 2000],
              ["top_k", "Passages per message", 1, 20],
            ] as const
          ).map(([key, label, min, max]) => (
            <Field key={key} label={label} htmlFor={`kb-${key}`}>
              <Input
                id={`kb-${key}`}
                type="number"
                min={min}
                max={max}
                value={chunk[key]}
                onChange={(e) => setChunk({ ...chunk, [key]: Number(e.target.value) })}
                onBlur={() => chunk[key] !== k[key] && save({ knowledge: { ...k, [key]: Math.min(max, Math.max(min, chunk[key] || min)) } })}
              />
            </Field>
          ))}
        </div>
      </Group>
    </>
  );
}

function DataSettings() {
  const [info, setInfo] = useState<Awaited<ReturnType<typeof api.info>> | null>(null);
  const [confirm, setConfirm] = useState<null | "conversations" | "all" | "restore">(null);
  const [pendingRestore, setPendingRestore] = useState<File | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLInputElement>(null);
  const loadConversations = useConversations((s) => s.load);
  const loadSettings = useSettings((s) => s.load);
  const loadModels = useModels((s) => s.load);

  useEffect(() => {
    api.info().then(setInfo).catch(() => undefined);
  }, []);

  const reloadAll = async () => {
    await Promise.all([loadConversations(), loadSettings(), loadModels()]);
  };

  return (
    <>
      <Group title="Your data stays here">
        <ul className="space-y-2 text-[0.8125rem] leading-relaxed text-muted">
          <li>• Conversations, documents and settings are stored in a local SQLite database on this server.</li>
          <li>• No telemetry, analytics or tracking. The interface loads no external fonts, scripts or styles.</li>
          <li>• Prompts are only sent to the model connections you configure.</li>
        </ul>
        {info && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 rounded-xl bg-subtle px-4 py-3 text-[0.8125rem]">
            <dt className="text-muted">Database</dt>
            <dd className="truncate font-mono text-fg" title={info.database}>
              {info.database}
            </dd>
            <dt className="text-muted">Size</dt>
            <dd className="text-fg">{formatBytes(info.db_size)}</dd>
            <dt className="text-muted">Access</dt>
            <dd className="text-fg">{info.auth_enabled ? "Password protected" : info.remote_access ? "Open on the network (no password)" : "This machine only"}</dd>
            <dt className="text-muted">Telemetry</dt>
            <dd className="text-fg">Off</dd>
          </dl>
        )}
      </Group>
      <Group title="Export & import" description="A portable JSON file with conversations, prompts and settings. API keys are never exported.">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" icon={<Download className="size-4" />} onClick={() => downloadUrl("/api/data/export")}>
            Export JSON
          </Button>
          <Button variant="outline" icon={<Upload className="size-4" />} onClick={() => importRef.current?.click()}>
            Import JSON
          </Button>
          <input
            ref={importRef}
            type="file"
            accept=".json,application/json"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              try {
                const r = await api.importData(file);
                toast(`Imported ${r.conversations} conversations, ${r.prompts} prompts${r.skipped ? ` (${r.skipped} already existed)` : ""}`, "success");
                await loadConversations();
              } catch (err) {
                toast((err as Error).message, "error");
              }
            }}
          />
        </div>
      </Group>
      <Group title="Backup & restore" description="A complete copy of the database, including connections and knowledge collections.">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" icon={<HardDrive className="size-4" />} onClick={() => downloadUrl("/api/data/backup")}>
            Download backup
          </Button>
          <Button variant="outline" icon={<Upload className="size-4" />} onClick={() => restoreRef.current?.click()}>
            Restore from backup
          </Button>
          <input
            ref={restoreRef}
            type="file"
            accept=".sqlite,.db,application/vnd.sqlite3,application/octet-stream"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) {
                setPendingRestore(file);
                setConfirm("restore");
              }
            }}
          />
        </div>
      </Group>
      <Group title="Delete data">
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" className="text-danger" onClick={() => setConfirm("conversations")}>
            Delete all conversations
          </Button>
          <Button variant="outline" className="text-danger" onClick={() => setConfirm("all")}>
            Clear all local data
          </Button>
        </div>
      </Group>
      <ConfirmDialog
        open={confirm === "conversations" || confirm === "all"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm === "all" ? "Clear all local data?" : "Delete all conversations?"}
        description={
          confirm === "all"
            ? "Conversations, prompts, knowledge collections, connections, uploads and settings will be permanently deleted. Consider downloading a backup first."
            : "Every conversation and message will be permanently deleted."
        }
        confirmLabel={confirm === "all" ? "Clear everything" : "Delete all"}
        onConfirm={async () => {
          try {
            await api.clearData(confirm === "all" ? "all" : "conversations");
            await reloadAll();
            toast("Data deleted");
          } catch (err) {
            toast((err as Error).message, "error");
          }
        }}
      />
      <ConfirmDialog
        open={confirm === "restore"}
        onOpenChange={(o) => {
          if (!o) {
            setConfirm(null);
            setPendingRestore(null);
          }
        }}
        title="Restore this backup?"
        description={`All current data will be replaced with the contents of “${pendingRestore?.name}”.`}
        confirmLabel="Restore"
        onConfirm={async () => {
          if (!pendingRestore) return;
          try {
            await api.restore(pendingRestore);
            await reloadAll();
            toast("Backup restored", "success");
          } catch (err) {
            toast((err as Error).message, "error");
          }
        }}
      />
    </>
  );
}

function About() {
  const [info, setInfo] = useState<{ version: string; auth_enabled: boolean } | null>(null);
  useEffect(() => {
    api.info().then(setInfo).catch(() => undefined);
  }, []);
  return (
    <Group title="Chimera" description="A calm, local-first workspace for your own language models.">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-[0.8125rem]">
        <dt className="text-muted">Version</dt>
        <dd className="font-mono text-fg">{info?.version ?? "…"}</dd>
        <dt className="text-muted">Inference</dt>
        <dd className="text-fg">llama.cpp (llama-server), OpenAI-compatible servers, Ollama</dd>
      </dl>
      {info?.auth_enabled && (
        <Button
          variant="outline"
          onClick={async () => {
            await api.logout();
            window.location.reload();
          }}
        >
          Sign out
        </Button>
      )}
    </Group>
  );
}
