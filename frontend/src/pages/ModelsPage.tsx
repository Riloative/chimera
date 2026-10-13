import { CheckCircle2, ChevronRight, CircleSlash, Eye, MoreHorizontal, Pencil, Plus, Power, RefreshCw, Trash2, Users, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { PageHeader, PageShell, Section } from "@/components/Page";
import { Button, ConfirmDialog, Dialog, Field, Input, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, Select, Spinner, StatusDot, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, providerErrorText, serverDetail } from "@/lib/errors";
import { relativeTime } from "@/lib/time";
import type { HealthCheck, Provider, ProviderKind, ProviderModels, TestResult } from "@/lib/types";
import { cn, compactNumber, formatBytes, formatList, modelLabel } from "@/lib/utils";
import { useAuth } from "@/stores/auth";
import { useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";

const KINDS: { value: ProviderKind; url: string; path: string }[] = [
  { value: "llamacpp", url: "http://127.0.0.1:8080", path: "/v1" },
  { value: "openai", url: "http://127.0.0.1:1234", path: "/v1" },
  { value: "ollama", url: "http://127.0.0.1:11434", path: "" },
];

/** A diagnostics line, translated from its code; server text shown verbatim. */
function CheckText({ check }: { check: HealthCheck }) {
  const { t } = useTranslation(["models", "common"]);
  const p = check.params ?? {};
  switch (check.code) {
    case "health_ready":
      return <>{t("checks.ready")}</>;
    case "endpoint_unavailable":
      return <>{t("checks.endpointUnavailable", { status: p.status as number })}</>;
    case "models_count":
      return <>{t("checks.models", { count: Number(p.count ?? 0) })}</>;
    case "props_available":
      return <>{t("checks.props")}</>;
    case "not_available":
      return <>{t("checks.notAvailable")}</>;
    case "version":
      return <>{String(p.version ?? check.detail)}</>;
    default:
      return <>{check.code ? providerErrorText(check.code, p, check.detail) : check.detail}</>;
  }
}

export function ModelsPage() {
  const { t } = useTranslation(["models", "common"]);
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
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <Button variant="ghost" icon={<RefreshCw className={cn("size-4", refreshing && "animate-spin")} />} onClick={() => load(true)} disabled={refreshing}>
              {t("refresh")}
            </Button>
            <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              {t("add")}
            </Button>
          </>
        }
      />

      <Section title={t("defaultModel")} description={t("defaultModelHelp")}>
        <Select
          aria-label={t("defaultModel")}
          value={defaultMissing ? "" : defaultValue}
          onChange={(e) => {
            const [pid, ...rest] = e.target.value.split("::");
            updateSettings({ default_provider_id: pid || null, default_model: rest.join("::") || null }).catch((err) => toast(errorText(err), "error"));
          }}
          className="max-w-md"
        >
          <option value="">{defaultMissing ? t("unavailableModel", { model: modelLabel(settings?.default_model) }) : t("lastUsed")}</option>
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

      <Section title={t("connections")}>
        {!loaded ? (
          <Spinner />
        ) : entries.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center">
            <p className="font-medium text-fg">{t("empty.title")}</p>
            <p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-muted">
              {t("empty.before")} <code className="rounded bg-subtle px-1 font-mono text-[0.8rem]">llama-server -m model.gguf --port 8080</code> {t("empty.after")}
            </p>
            <Button className="mt-4" variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
              {t("add")}
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
  const { t } = useTranslation(["models", "common"]);
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
      toast(
        r.ok ? t("toast.connected", { name: p.name }) : t("toast.failed", { name: p.name, error: providerErrorText(r.error_code, r.error_params, r.error) }),
        r.ok ? "success" : "error",
      );
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setTesting(false);
    }
  };

  const toggle = async () => {
    try {
      setProvider(await api.updateProvider(p.id, { enabled: !p.enabled }));
      await load(!p.enabled);
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const caps = result?.capabilities ?? p.capabilities;
  const visionText =
    p.vision !== "auto" ? t(p.vision === "on" ? "visionManualOn" : "visionManualOff") : caps.vision == null ? t("visionUnknown") : caps.vision ? t("visionYes") : t("visionNo");

  return (
    <li>
      <div className="flex items-center gap-3 px-4 py-3.5">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-label={open ? t("hideDiagnostics", { name: p.name }) : t("showDiagnostics", { name: p.name })}
          className="flex size-7 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-fg"
        >
          <ChevronRight className={cn("size-4 transition-transform", open && "rotate-90")} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className={cn("truncate text-sm font-medium", p.enabled ? "text-fg" : "text-muted")} title={p.name}>
              {p.name}
            </span>
            <span className="shrink-0 rounded-md bg-subtle px-1.5 py-0.5 text-[0.7rem] text-muted">{t(`kinds.${p.kind}.label`)}</span>
            {p.shared && (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[0.7rem] text-accent-text">
                <Users className="size-3" aria-hidden /> {t("shared")}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.75rem] text-muted">
            <span className="truncate font-mono">{p.base_url}</span>
            <StatusDot status={p.status} withLabel />
            {p.latency_ms != null && p.status === "connected" && <span>{t("ms", { value: Math.round(p.latency_ms) })}</span>}
            <span className="text-faint">{t("checked", { when: p.checked_at ? relativeTime(p.checked_at) : t("never") })}</span>
            {p.status !== "disabled" && <span className="text-faint">{t("modelCount", { count: entry.models.length })}</span>}
          </div>
          {p.status === "error" && (p.last_error || p.last_error_code) && (
            <p className="mt-1 text-[0.75rem] text-danger">{providerErrorText(p.last_error_code, p.last_error_params, p.last_error)}</p>
          )}
        </div>
        <Button size="sm" variant="outline" onClick={test} loading={testing} disabled={!p.enabled}>
          {t("test")}
        </Button>
        {p.editable && (
          <MenuRoot>
            <MenuTrigger asChild>
              <button type="button" aria-label={t("moreOptions", { name: p.name })} className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-fg">
                <MoreHorizontal className="size-4" />
              </button>
            </MenuTrigger>
            <MenuContent align="end">
              <MenuItem icon={<Pencil />} onSelect={onEdit}>
                {t("common:edit")}
              </MenuItem>
              <MenuItem icon={p.enabled ? <CircleSlash /> : <Power />} onSelect={toggle}>
                {p.enabled ? t("disable") : t("enable")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem icon={<Trash2 />} destructive onSelect={() => setConfirm(true)}>
                {t("common:delete")}
              </MenuItem>
            </MenuContent>
          </MenuRoot>
        )}
      </div>

      {open && (
        <div className="animate-fade-in border-t border-line bg-bg/60 px-4 py-4 text-[0.8125rem] sm:pl-14">
          <div className="grid gap-6 md:grid-cols-2">
            <div className="min-w-0">
              <h4 className="mb-2 text-[0.72rem] font-medium tracking-wide text-faint uppercase">{t("diagnostics")}</h4>
              {result ? (
                <ul className="space-y-1.5">
                  {result.checks.map((c) => (
                    <li key={c.name} className="flex items-start gap-2">
                      {c.ok === true ? (
                        <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-label={t("checkPassed")} />
                      ) : c.ok === false ? (
                        <XCircle className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label={t("checkFailed")} />
                      ) : (
                        <CircleSlash className="mt-0.5 size-3.5 shrink-0 text-faint" aria-label={t("checkUnavailable")} />
                      )}
                      <span className="font-mono text-muted">{c.name}</span>
                      <span className="min-w-0 break-words text-fg">
                        <CheckText check={c} />
                        {c.ok === false && serverDetail(c.params) && (
                          <span className="block font-mono text-[0.75rem] text-faint">{serverDetail(c.params)}</span>
                        )}
                      </span>
                      {c.latency_ms != null && <span className="shrink-0 text-faint">{t("ms", { value: Math.round(c.latency_ms) })}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted">{t("runTest")}</p>
              )}
              <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-muted">
                <dt>{t("endpoint")}</dt>
                <dd className="truncate font-mono text-fg">{p.base_url + (p.api_path || "")}</dd>
                <dt>{t("auth")}</dt>
                <dd className="text-fg">{p.has_api_key ? t("authKey", { header: p.auth_header || "Authorization" }) : t("authNone")}</dd>
                <dt>{t("timeout")}</dt>
                <dd className="text-fg">{t("seconds", { value: p.timeout })}</dd>
                {caps.n_ctx != null && (
                  <>
                    <dt>{t("context")}</dt>
                    <dd className="text-fg">{t("contextTokens", { value: compactNumber(caps.n_ctx) })}</dd>
                  </>
                )}
                {caps.slots != null && (
                  <>
                    <dt>{t("slots")}</dt>
                    <dd className="text-fg">{caps.slots}</dd>
                  </>
                )}
                <dt>{t("vision")}</dt>
                <dd className="text-fg">{visionText}</dd>
                {caps.build && (
                  <>
                    <dt>{t("build")}</dt>
                    <dd className="truncate font-mono text-fg">{caps.build}</dd>
                  </>
                )}
                <dt>{t("parameters")}</dt>
                <dd className="font-mono text-[0.75rem] break-words text-fg">{formatList(p.supported_params)}</dd>
              </dl>
            </div>
            <div className="min-w-0">
              <h4 className="mb-2 text-[0.72rem] font-medium tracking-wide text-faint uppercase">{t("title")}</h4>
              {entry.error && <p className="mb-2 text-danger">{providerErrorText(entry.error_code, entry.error_params, entry.error)}</p>}
              {entry.models.length === 0 ? (
                <p className="text-muted">{t("noModelsReported")}</p>
              ) : (
                <ul className="space-y-1">
                  {entry.models.map((m) => (
                    <li key={m.id} className="flex items-center gap-2">
                      <span className="truncate font-mono text-fg" title={m.id}>
                        {m.name || m.id}
                      </span>
                      {m.vision && <Eye className="size-3 shrink-0 text-muted" aria-label={t("vision")} />}
                      <span className="ml-auto shrink-0 text-faint">
                        {m.source === "manual" ? t("manual") : m.context_length ? t("ctx", { value: compactNumber(m.context_length) }) : ""}
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
        title={t("deleteTitle", { name: p.name })}
        description={p.shared ? t("deleteSharedBody") : t("deleteBody")}
        onConfirm={async () => {
          try {
            await api.deleteProvider(p.id);
            removeProvider(p.id);
            toast(t("toast.deleted"));
          } catch (err) {
            toast(errorText(err), "error");
          }
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
  shared: boolean;
}

function initialForm(p: Provider | null): FormState {
  if (!p)
    return { name: "llama-server", kind: "llamacpp", base_url: KINDS[0].url, api_path: "/v1", api_key: "", auth_header: "", timeout: 300, vision: "auto", manual_models: "", enabled: true, shared: false };
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
    shared: p.shared,
  };
}

function ProviderDialog({ open, provider, onClose }: { open: boolean; provider: Provider | null; onClose: () => void }) {
  const { t } = useTranslation(["models", "common"]);
  const isAdmin = useAuth((s) => s.user?.role === "admin");
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
    if (isAdmin) data.shared = form.shared;
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
      setError(errorText(err));
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
      toast(provider ? t("toast.updated") : t("toast.added"), "success");
      onClose();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  const kind = KINDS.find((k) => k.value === form.kind)!;
  const defaultNames = new Set(["llama-server", ...KINDS.map((k) => t(`kinds.${k.value}.label`))]);

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={provider ? t("dialog.editTitle", { name: provider.name }) : t("add")}
      description={t(`kinds.${form.kind}.help`)}
      footer={
        <>
          <Button variant="outline" onClick={test} loading={testing} className="mr-auto">
            {t("dialog.test")}
          </Button>
          <Button variant="ghost" onClick={onClose}>
            {t("common:cancel")}
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!form.name.trim() || !form.base_url.trim()}>
            {provider ? t("common:save") : t("dialog.add")}
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
          <Field label={t("dialog.type")} htmlFor="pv-kind">
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
                  name: defaultNames.has(f.name) ? (k.value === "llamacpp" ? "llama-server" : t(`kinds.${k.value}.label`)) : f.name,
                }));
              }}
            >
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {t(`kinds.${k.value}.label`)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("dialog.name")} htmlFor="pv-name">
            <Input id="pv-name" value={form.name} onChange={(e) => set("name", e.target.value)} maxLength={80} placeholder={t("dialog.namePlaceholder")} />
          </Field>
        </div>
        <Field
          label={t("dialog.url")}
          htmlFor="pv-url"
          hint={
            <>
              {t("dialog.urlHint")} <code className="font-mono">http://host.docker.internal:8080</code>
            </>
          }
        >
          <Input id="pv-url" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder={kind.url} className="font-mono" spellCheck={false} />
        </Field>
        <Field label={t("dialog.apiKey")} htmlFor="pv-key" hint={provider?.has_api_key ? t("dialog.keyStored") : t("dialog.keyHint")}>
          <div className="flex gap-2">
            <Input
              id="pv-key"
              type="password"
              autoComplete="off"
              value={form.api_key}
              onChange={(e) => set("api_key", e.target.value)}
              placeholder={provider?.has_api_key && !clearKey ? "••••••••" : t("dialog.optional")}
            />
            {provider?.has_api_key && (
              <Button type="button" variant="ghost" size="md" onClick={() => setClearKey((c) => !c)}>
                {clearKey ? t("dialog.keepKey") : t("dialog.removeKey")}
              </Button>
            )}
          </div>
        </Field>
        {isAdmin && (
          <label className="flex items-start gap-2.5 text-sm text-fg">
            <input type="checkbox" checked={form.shared} onChange={(e) => set("shared", e.target.checked)} className="mt-1 accent-[var(--accent)]" />
            <span>
              {t("dialog.share")}
              <span className="block text-[0.75rem] text-muted">{t("dialog.shareHint")}</span>
            </span>
          </label>
        )}

        <button type="button" onClick={() => setAdvanced((a) => !a)} className="flex items-center gap-1 text-[0.8125rem] text-muted hover:text-fg" aria-expanded={advanced}>
          <ChevronRight className={cn("size-3.5 transition-transform", advanced && "rotate-90")} /> {t("dialog.advanced")}
        </button>
        {advanced && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("dialog.apiPath")} htmlFor="pv-path" hint={t("dialog.apiPathHint")}>
                <Input id="pv-path" value={form.api_path} onChange={(e) => set("api_path", e.target.value)} placeholder="/v1" className="font-mono" disabled={form.kind === "ollama"} />
              </Field>
              <Field label={t("dialog.authHeader")} htmlFor="pv-header" hint={t("dialog.authHeaderHint")}>
                <Input id="pv-header" value={form.auth_header} onChange={(e) => set("auth_header", e.target.value)} placeholder="Authorization" className="font-mono" />
              </Field>
              <Field label={t("dialog.timeout")} htmlFor="pv-timeout" hint={t("dialog.timeoutHint")}>
                <Input id="pv-timeout" type="number" min={1} max={3600} value={form.timeout} onChange={(e) => set("timeout", Number(e.target.value) || 300)} />
              </Field>
              <Field label={t("dialog.imageInput")} htmlFor="pv-vision" hint={t("dialog.imageInputHint")}>
                <Select id="pv-vision" value={form.vision} onChange={(e) => set("vision", e.target.value as FormState["vision"])}>
                  <option value="auto">{t("dialog.visionAuto")}</option>
                  <option value="on">{t("dialog.visionOn")}</option>
                  <option value="off">{t("dialog.visionOff")}</option>
                </Select>
              </Field>
            </div>
            <Field label={t("dialog.manualModels")} htmlFor="pv-manual" hint={t("dialog.manualModelsHint")}>
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
            {result.ok
              ? result.latency_ms != null
                ? t("dialog.connectedMs", { value: Math.round(result.latency_ms) })
                : t("dialog.connected")
              : t("dialog.failed")}
          </p>
          {(result.error || result.error_code) && <p className="mt-1 text-muted">{providerErrorText(result.error_code, result.error_params, result.error)}</p>}
          {result.ok && (
            <p className="mt-1 text-muted">
              {t("dialog.foundModels", { count: result.models.length, list: formatList(result.models.slice(0, 4).map((m) => m.name || m.id)) })}
              {result.models.length > 4 ? "…" : ""}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
