import { CheckCircle2, ChevronRight, CircleSlash, Eye, MoreHorizontal, Pencil, Plus, Power, RefreshCw, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { PageHeader, PageShell, Section } from "@/components/Page";
import { Button, ConfirmDialog, Dialog, Field, Input, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, Select, Spinner, StatusDot, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/time";
import type { Provider, ProviderKind, ProviderModels, TestResult } from "@/lib/types";
import { cn, compactNumber, formatBytes, modelLabel } from "@/lib/utils";
import { useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";

const KINDS: { value: ProviderKind; label: string; url: string; path: string; help: string }[] = [
  {
    value: "llamacpp",
    label: "llama.cpp server",
    url: "http://127.0.0.1:8080",
    path: "/v1",
    help: "llama-server's OpenAI-compatible API plus /health and /props for diagnostics.",
  },
  {
    value: "openai",
    label: "OpenAI-compatible",
    url: "http://127.0.0.1:1234",
    path: "/v1",
    help: "LM Studio, vLLM, LocalAI, llamafile, or any server exposing /v1/models and /v1/chat/completions.",
  },
  { value: "ollama", label: "Ollama", url: "http://127.0.0.1:11434", path: "", help: "Ollama's native API (/api/tags, /api/chat)." },
];
const kindLabel = (k: ProviderKind) => KINDS.find((x) => x.value === k)?.label ?? k;

export function ModelsPage() {
  const { entries, load, refreshing, loaded } = useModels();
  const settings = useSettings((s) => s.settings);
  const updateSettings = useSettings((s) => s.update);
  const [editing, setEditing] = useState<Provider | "new" | null>(null);

  useEffect(() => {
    void load(false);
  }, [load]);

  const allModels = entries.flatMap((e) => e.models.map((m) => ({ provider: e.provider, model: m })));
  const defaultValue = settings?.default_provider_id && settings.default_model ? `${settings.default_provider_id}::${settings.default_model}` : "";
  const defaultMissing = !!defaultValue && !allModels.some((x) => `${x.provider.id}::${x.model.id}` === defaultValue);

  return (
    <PageShell>
      <PageHeader
        title="Models"
        description="Connect Chimera to inference servers running on your machine or network. Chimera never loads model weights itself."
        actions={
          <>
            <Button variant="ghost" icon={<RefreshCw className={cn("size-4", refreshing && "animate-spin")} />} onClick={() => load(true)} disabled={refreshing}>
              Refresh
            </Button>
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              Add connection
            </Button>
          </>
        }
      />

      <Section title="Default model" description="Used for new conversations. You can switch models at any time from the composer.">
        <Select
          aria-label="Default model"
          value={defaultMissing ? "" : defaultValue}
          onChange={(e) => {
            const [pid, ...rest] = e.target.value.split("::");
            updateSettings({ default_provider_id: pid || null, default_model: rest.join("::") || null }).catch((err) => toast(err.message, "error"));
          }}
          className="max-w-md"
        >
          <option value="">{defaultMissing ? `${modelLabel(settings?.default_model)} (unavailable)` : "Last used model"}</option>
          {entries.map((e) =>
            e.models.length ? (
              <optgroup key={e.provider.id} label={e.provider.name}>
                {e.models.map((m) => (
                  <option key={m.id} value={`${e.provider.id}::${m.id}`}>
                    {m.name || m.id}
                  </option>
                ))}
              </optgroup>
            ) : null,
          )}
        </Select>
      </Section>

      <Section title="Connections">
        {!loaded ? (
          <Spinner />
        ) : entries.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
            <p className="font-medium text-fg">No connections yet</p>
            <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-muted">
              Start a model with <code className="rounded bg-subtle px-1 font-mono text-[0.8rem]">llama-server -m model.gguf --port 8080</code> and add it here.
            </p>
            <Button className="mt-4" variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              Add connection
            </Button>
          </div>
        ) : (
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-elevated">
            {entries.map((e) => (
              <ConnectionRow key={e.provider.id} entry={e} onEdit={() => setEditing(e.provider)} />
            ))}
          </ul>
        )}
      </Section>

      <ProviderDialog open={editing !== null} provider={editing === "new" ? null : editing} onClose={() => setEditing(null)} />
    </PageShell>
  );
}

function ConnectionRow({ entry, onEdit }: { entry: ProviderModels; onEdit: () => void }) {
  const p = entry.provider;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const { load, setProvider, removeProvider } = useModels();

  const test = async () => {
    setTesting(true);
    try {
      const r = await api.testProvider(p.id);
      setResult(r);
      setOpen(true);
      if (r.provider) setProvider(r.provider);
      await load(false);
      toast(r.ok ? `${p.name}: connected` : `${p.name}: ${r.error}`, r.ok ? "success" : "error");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };

  const toggle = async () => {
    try {
      setProvider(await api.updateProvider(p.id, { enabled: !p.enabled }));
      await load(p.enabled ? false : true);
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  const caps = result?.capabilities ?? p.capabilities;

  return (
    <li>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} diagnostics for ${p.name}`}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-fg"
        >
          <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={cn("truncate text-sm font-medium", p.enabled ? "text-fg" : "text-muted")}>{p.name}</span>
            <span className="shrink-0 rounded-md bg-subtle px-1.5 py-0.5 text-[0.7rem] text-muted">{kindLabel(p.kind)}</span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.75rem] text-muted">
            <span className="truncate font-mono">{p.base_url}</span>
            <StatusDot status={p.status} withLabel />
            {p.latency_ms != null && p.status === "connected" && <span>{Math.round(p.latency_ms)} ms</span>}
            <span className="text-faint">checked {relativeTime(p.checked_at)}</span>
            {p.status !== "disabled" && <span className="text-faint">{entry.models.length} model{entry.models.length === 1 ? "" : "s"}</span>}
          </div>
          {p.status === "error" && p.last_error && <p className="mt-1 text-[0.75rem] text-danger">{p.last_error}</p>}
        </div>
        <Button size="sm" variant="outline" onClick={test} loading={testing} disabled={!p.enabled}>
          Test
        </Button>
        <MenuRoot>
          <MenuTrigger asChild>
            <button type="button" aria-label={`More options for ${p.name}`} className="flex size-8 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-fg">
              <MoreHorizontal className="size-4" />
            </button>
          </MenuTrigger>
          <MenuContent align="end">
            <MenuItem icon={<Pencil />} onSelect={onEdit}>
              Edit
            </MenuItem>
            <MenuItem icon={p.enabled ? <CircleSlash /> : <Power />} onSelect={toggle}>
              {p.enabled ? "Disable" : "Enable"}
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} destructive onSelect={() => setConfirm(true)}>
              Delete
            </MenuItem>
          </MenuContent>
        </MenuRoot>
      </div>

      {open && (
        <div className="animate-fade-in border-t border-line bg-bg/60 px-4 py-4 pl-14 text-[0.8125rem]">
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <h4 className="mb-2 text-[0.72rem] font-medium tracking-wide text-faint uppercase">Diagnostics</h4>
              {result ? (
                <ul className="space-y-1.5">
                  {result.checks.map((c) => (
                    <li key={c.name} className="flex items-start gap-2">
                      {c.ok === true ? (
                        <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="Passed" />
                      ) : c.ok === false ? (
                        <XCircle className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label="Failed" />
                      ) : (
                        <CircleSlash className="mt-0.5 size-3.5 shrink-0 text-faint" aria-label="Not available" />
                      )}
                      <span className="font-mono text-muted">{c.name}</span>
                      <span className="text-fg">{c.detail}</span>
                      {c.latency_ms != null && <span className="text-faint">{Math.round(c.latency_ms)} ms</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted">Run a test to see endpoint checks.</p>
              )}
              <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-muted">
                <dt>Endpoint</dt>
                <dd className="truncate font-mono text-fg">{p.base_url + (p.api_path || "")}</dd>
                <dt>Auth</dt>
                <dd className="text-fg">{p.has_api_key ? `API key via ${p.auth_header || "Authorization"}` : "none"}</dd>
                <dt>Timeout</dt>
                <dd className="text-fg">{p.timeout}s</dd>
                {caps.n_ctx != null && (
                  <>
                    <dt>Context</dt>
                    <dd className="text-fg">{compactNumber(caps.n_ctx)} tokens</dd>
                  </>
                )}
                {caps.slots != null && (
                  <>
                    <dt>Slots</dt>
                    <dd className="text-fg">{caps.slots}</dd>
                  </>
                )}
                <dt>Vision</dt>
                <dd className="text-fg">
                  {p.vision !== "auto" ? `${p.vision === "on" ? "on" : "off"} (manual)` : caps.vision == null ? "unknown" : caps.vision ? "supported" : "not supported"}
                </dd>
                {caps.build && (
                  <>
                    <dt>Build</dt>
                    <dd className="truncate font-mono text-fg">{caps.build}</dd>
                  </>
                )}
                <dt>Parameters</dt>
                <dd className="text-fg">{p.supported_params.join(", ")}</dd>
              </dl>
            </div>
            <div>
              <h4 className="mb-2 text-[0.72rem] font-medium tracking-wide text-faint uppercase">Models</h4>
              {entry.error && <p className="mb-2 text-danger">{entry.error}</p>}
              {entry.models.length === 0 ? (
                <p className="text-muted">No models reported.</p>
              ) : (
                <ul className="space-y-1">
                  {entry.models.map((m) => (
                    <li key={m.id} className="flex items-center gap-2">
                      <span className="truncate font-mono text-fg" title={m.id}>
                        {m.name || m.id}
                      </span>
                      {m.vision && <Eye className="size-3 shrink-0 text-muted" aria-label="Vision" />}
                      <span className="ml-auto shrink-0 text-faint">
                        {m.source === "manual" ? "manual" : m.context_length ? `${compactNumber(m.context_length)} ctx` : ""}
                        {typeof m.meta?.size === "number" ? ` · ${formatBytes(m.meta.size as number)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Delete “${p.name}”?`}
        description="Conversations that used this connection are kept, but you'll need to pick another model to continue them."
        onConfirm={async () => {
          await api.deleteProvider(p.id);
          removeProvider(p.id);
          toast("Connection deleted");
        }}
      />
    </li>
  );
}

interface FormState {
  name: string;
  kind: ProviderKind;
  base_url: string;
  api_path: string;
  api_key: string;
  auth_header: string;
  timeout: number;
  vision: "auto" | "on" | "off";
  manual_models: string;
  enabled: boolean;
}

function initialForm(p: Provider | null): FormState {
  if (!p) return { name: "llama-server", kind: "llamacpp", base_url: KINDS[0].url, api_path: "/v1", api_key: "", auth_header: "", timeout: 300, vision: "auto", manual_models: "", enabled: true };
  return {
    name: p.name,
    kind: p.kind,
    base_url: p.base_url,
    api_path: p.api_path,
    api_key: "",
    auth_header: p.auth_header,
    timeout: p.timeout,
    vision: p.vision,
    manual_models: p.manual_models.join("\n"),
    enabled: p.enabled,
  };
}

function ProviderDialog({ open, provider, onClose }: { open: boolean; provider: Provider | null; onClose: () => void }) {
  const [form, setForm] = useState<FormState>(initialForm(provider));
  const [advanced, setAdvanced] = useState(false);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [clearKey, setClearKey] = useState(false);
  const { load, setProvider } = useModels();

  useEffect(() => {
    if (open) {
      setForm(initialForm(provider));
      setResult(null);
      setError(null);
      setClearKey(false);
      setAdvanced(false);
    }
  }, [open, provider]);

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => setForm((f) => ({ ...f, [k]: v }));

  const payload = () => {
    const data: Record<string, unknown> = {
      name: form.name.trim(),
      kind: form.kind,
      base_url: form.base_url.trim(),
      api_path: form.api_path.trim(),
      auth_header: form.auth_header.trim(),
      timeout: form.timeout,
      vision: form.vision,
      manual_models: form.manual_models.split("\n").map((s) => s.trim()).filter(Boolean),
      enabled: form.enabled,
    };
    if (form.api_key) data.api_key = form.api_key;
    else if (clearKey) data.api_key = "";
    return data;
  };

  const test = async () => {
    setTesting(true);
    setError(null);
    try {
      // Test exactly what is typed without saving (the stored key is reused if untouched).
      const data = payload();
      if (provider && !form.api_key && !clearKey) data.provider_id = provider.id;
      setResult(await api.testUnsaved(data));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = provider ? await api.updateProvider(provider.id, payload()) : await api.createProvider(payload());
      setProvider(saved);
      // Verify right away so the status reflects reality.
      await api.testProvider(saved.id).catch(() => undefined);
      await load(false);
      toast(provider ? "Connection updated" : "Connection added", "success");
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const kind = KINDS.find((k) => k.value === form.kind)!;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={provider ? `Edit ${provider.name}` : "Add connection"}
      description={kind.help}
      footer={
        <>
          <Button variant="outline" onClick={test} loading={testing} className="mr-auto">
            Test connection
          </Button>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!form.name.trim() || !form.base_url.trim()}>
            {provider ? "Save" : "Add"}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4 pb-3"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Type" htmlFor="pv-kind">
            <Select
              id="pv-kind"
              value={form.kind}
              onChange={(e) => {
                const k = KINDS.find((x) => x.value === e.target.value)!;
                setForm((f) => ({
                  ...f,
                  kind: k.value,
                  base_url: KINDS.some((x) => x.url === f.base_url) ? k.url : f.base_url,
                  api_path: k.path,
                  name: KINDS.some((x) => x.label === f.name) || f.name === "llama-server" ? (k.value === "llamacpp" ? "llama-server" : k.label) : f.name,
                }));
              }}
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Name" htmlFor="pv-name">
            <Input id="pv-name" value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={80} placeholder="Local coding model" />
          </Field>
        </div>
        <Field
          label="Server URL"
          htmlFor="pv-url"
          hint={
            <>
              Where the server listens. From Docker, use <code className="font-mono">http://host.docker.internal:8080</code> to reach your host.
            </>
          }
        >
          <Input id="pv-url" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder={kind.url} className="font-mono" spellCheck={false} />
        </Field>
        <Field label="API key" htmlFor="pv-key" hint={provider?.has_api_key ? "A key is stored. Leave empty to keep it." : "Only if the server was started with --api-key. Stored locally, never shown again."}>
          <div className="flex gap-2">
            <Input
              id="pv-key"
              type="password"
              autoComplete="off"
              value={form.api_key}
              onChange={(e) => set("api_key", e.target.value)}
              placeholder={provider?.has_api_key && !clearKey ? "••••••••" : "Optional"}
            />
            {provider?.has_api_key && (
              <Button type="button" variant="ghost" size="md" onClick={() => setClearKey((c) => !c)}>
                {clearKey ? "Keep key" : "Remove key"}
              </Button>
            )}
          </div>
        </Field>

        <button type="button" onClick={() => setAdvanced((a) => !a)} className="flex items-center gap-1 text-[0.8125rem] text-muted hover:text-fg" aria-expanded={advanced}>
          <ChevronRight className={cn("size-3.5 transition-transform", advanced && "rotate-90")} /> Advanced
        </button>
        {advanced && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="API path" htmlFor="pv-path" hint="Appended to the URL for OpenAI-style endpoints.">
                <Input id="pv-path" value={form.api_path} onChange={(e) => set("api_path", e.target.value)} placeholder="/v1" className="font-mono" disabled={form.kind === "ollama"} />
              </Field>
              <Field label="Auth header" htmlFor="pv-header" hint="Defaults to Authorization: Bearer …">
                <Input id="pv-header" value={form.auth_header} onChange={(e) => set("auth_header", e.target.value)} placeholder="Authorization" className="font-mono" />
              </Field>
              <Field label="Timeout (seconds)" htmlFor="pv-timeout" hint="Max wait for the first/next token. Long prompts on slow hardware need more.">
                <Input id="pv-timeout" type="number" min={1} max={3600} value={form.timeout} onChange={(e) => set("timeout", Number(e.target.value) || 300)} />
              </Field>
              <Field label="Image input" htmlFor="pv-vision" hint="Auto uses what the server reports (llama-server /props).">
                <Select id="pv-vision" value={form.vision} onChange={(e) => set("vision", e.target.value as FormState["vision"])}>
                  <option value="auto">Auto-detect</option>
                  <option value="on">Supported</option>
                  <option value="off">Not supported</option>
                </Select>
              </Field>
            </div>
            <Field label="Manual models" htmlFor="pv-manual" hint="One model ID per line. Use only if the server cannot list its models.">
              <Textarea id="pv-manual" rows={3} value={form.manual_models} onChange={(e) => set("manual_models", e.target.value)} className="font-mono" />
            </Field>
          </div>
        )}
        <button type="submit" className="hidden" />
      </form>

      {error && <p className="mb-3 rounded-lg bg-danger-soft px-3 py-2 text-[0.8125rem] text-danger">{error}</p>}
      {result && (
        <div className={cn("mb-3 rounded-xl border px-3.5 py-3 text-[0.8125rem]", result.ok ? "border-success/30" : "border-danger/30 bg-danger-soft")}>
          <p className="flex items-center gap-2 font-medium text-fg">
            {result.ok ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-danger" />}
            {result.ok ? `Connected${result.latency_ms != null ? ` · ${Math.round(result.latency_ms)} ms` : ""}` : "Connection failed"}
          </p>
          {result.error && <p className="mt-1 text-muted">{result.error}</p>}
          {result.ok && (
            <p className="mt-1 text-muted">
              {result.models.length} model{result.models.length === 1 ? "" : "s"}: {result.models.slice(0, 4).map((m) => m.name || m.id).join(", ")}
              {result.models.length > 4 ? "…" : ""}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
