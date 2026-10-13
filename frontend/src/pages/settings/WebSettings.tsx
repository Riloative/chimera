import { CheckCircle2, Loader2, Search, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Group } from "@/components/Page";
import { Button, Field, Input, Select, Spinner, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, providerErrorText } from "@/lib/errors";
import { relativeTime } from "@/lib/time";
import type { AppSettings, WebAdminSettings, WebAdminView, WebEngineKind, WebEngineTest, WebUserSettings } from "@/lib/types";
import { useAuth } from "@/stores/auth";
import { useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";
import { useWeb } from "@/stores/web";

const ENGINES: WebEngineKind[] = ["searxng", "brave", "tavily"];
const lines = (v: string) => v.split("\n").map((s) => s.trim()).filter(Boolean);

export function WebSettings({ settings }: { settings: AppSettings }) {
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  return (
    <>
      <UserWebSettings settings={settings} />
      {isAdmin && <AdminWebSettings />}
    </>
  );
}

/* ------------------------------------------------------------------ user */
function UserWebSettings({ settings }: { settings: AppSettings }) {
  const { t } = useTranslation(["web", "common"]);
  const status = useWeb((s) => s.status);
  const update = useSettings((s) => s.update);
  const entries = useModels((s) => s.entries);
  const w = settings.web;
  const [ctx, setCtx] = useState(w.context_tokens);
  useEffect(() => setCtx(w.context_tokens), [w.context_tokens]);
  const save = (patch: Partial<WebUserSettings>) => update({ web: { ...w, ...patch } }).catch((e) => toast(errorText(e), "error"));
  const max = status?.max_results ?? 10;
  const taskValue = w.task_provider_id && w.task_model ? `${w.task_provider_id}\u0000${w.task_model}` : "";

  return (
    <Group title={t("settings.user.title")} description={t("settings.user.help")}>
      {status && !status.enabled && <p className="rounded-lg bg-subtle px-3 py-2 text-[0.8125rem] text-muted">{t("settings.user.disabled")}</p>}
      {status?.enabled && !status.search_available && <p className="rounded-lg bg-subtle px-3 py-2 text-[0.8125rem] text-muted">{t("settings.user.noSearch")}</p>}
      <Switch label={t("settings.user.autoRead")} description={t("settings.user.autoReadHelp")} checked={w.auto_read_links} onChange={(v) => save({ auto_read_links: v })} />
      <Switch label={t("settings.user.searchDefault")} description={t("settings.user.searchDefaultHelp")} checked={w.search_by_default} onChange={(v) => save({ search_by_default: v })} />
      <Switch label={t("settings.user.keepOn")} description={t("settings.user.keepOnHelp")} checked={w.keep_search_on} onChange={(v) => save({ keep_search_on: v })} />
      <Switch label={t("settings.user.expandSteps")} checked={w.expand_steps} onChange={(v) => save({ expand_steps: v })} />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("settings.user.results")} htmlFor="web-results" hint={t("settings.user.resultsHelp", { max })}>
          <Select id="web-results" value={Math.min(w.results, max)} onChange={(e) => save({ results: Number(e.target.value) })}>
            {Array.from({ length: max }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t("settings.user.contextTokens")} htmlFor="web-ctx" hint={t("settings.user.contextTokensHelp")}>
          <Input
            id="web-ctx"
            type="number"
            min={1024}
            max={2000000}
            step={1024}
            value={ctx}
            onChange={(e) => setCtx(Number(e.target.value))}
            onBlur={() => ctx !== w.context_tokens && save({ context_tokens: Math.min(2000000, Math.max(1024, ctx || 8192)) })}
          />
        </Field>
      </div>
      <Field label={t("settings.user.taskModel")} htmlFor="web-task" hint={t("settings.user.taskModelHelp")}>
        <Select
          id="web-task"
          value={taskValue}
          onChange={(e) => {
            const [pid, model] = e.target.value ? e.target.value.split("\u0000") : [null, null];
            void save({ task_provider_id: pid, task_model: model });
          }}
        >
          <option value="">{t("settings.user.taskModelDefault")}</option>
          {entries.map((entry) =>
            entry.models.length ? (
              <optgroup key={entry.provider.id} label={entry.provider.name}>
                {entry.models.map((m) => (
                  <option key={m.id} value={`${entry.provider.id}\u0000${m.id}`}>
                    {m.name ?? m.id}
                  </option>
                ))}
              </optgroup>
            ) : null,
          )}
        </Select>
      </Field>
      {w.privacy_acknowledged && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-[0.8125rem] text-muted">
          <span>{t("settings.user.privacyAcknowledged")}</span>
          <Button size="sm" variant="ghost" onClick={() => save({ privacy_acknowledged: false })}>
            {t("settings.user.privacyShow")}
          </Button>
        </div>
      )}
    </Group>
  );
}

/* ----------------------------------------------------------------- admin */
function AdminWebSettings() {
  const { t } = useTranslation(["web", "common"]);
  const [view, setView] = useState<WebAdminView | null>(null);
  const reloadStatus = useWeb((s) => s.load);

  useEffect(() => {
    api.adminWeb().then(setView).catch((e) => toast(errorText(e), "error"));
  }, []);

  const save = async (data: { settings?: Partial<WebAdminSettings>; engine_keys?: Partial<Record<WebEngineKind, string | null>> }, quiet = false) => {
    try {
      const next = await api.adminUpdateWeb(data);
      setView(next);
      await reloadStatus();
      if (!quiet) toast(t("settings.admin.saved"), "success");
      return true;
    } catch (err) {
      toast(errorText(err), "error");
      return false;
    }
  };

  if (!view) return <Spinner />;
  const s = view.settings;
  return (
    <>
      <Group title={t("settings.admin.title")} description={t("settings.admin.help")}>
        <Switch label={t("settings.admin.enabled")} description={t("settings.admin.enabledHelp")} checked={s.enabled} onChange={(v) => save({ settings: { enabled: v } })} />
      </Group>
      <EngineSettings view={view} save={save} />
      <PipelineSettings settings={s} defaults={view.defaults} save={save} />
      <NetworkSettings settings={s} save={save} />
      <LimitSettings settings={s} save={save} />
    </>
  );
}

type Save = (data: { settings?: Partial<WebAdminSettings>; engine_keys?: Partial<Record<WebEngineKind, string | null>> }, quiet?: boolean) => Promise<boolean>;

function EngineSettings({ view, save }: { view: WebAdminView; save: Save }) {
  const { t } = useTranslation(["web", "common"]);
  const s = view.settings;
  const [engine, setEngine] = useState<WebEngineKind | "">(s.engine ?? "");
  const [url, setUrl] = useState(s.searxng_url);
  const [header, setHeader] = useState(s.searxng_auth_header);
  const [key, setKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<WebEngineTest | null>(null);
  const info = engine ? view.engines.find((e) => e.kind === engine) : undefined;
  const last = result ?? info?.status ?? null;

  useEffect(() => {
    setKey("");
    setResult(null);
  }, [engine]);

  const test = async () => {
    if (!engine) return;
    setTesting(true);
    try {
      setResult(await api.adminTestWeb({ kind: engine, searxng_url: url, searxng_auth_header: header, api_key: key || undefined }));
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setTesting(false);
    }
  };

  const submit = async () => {
    const ok = await save({
      settings: { engine: engine || null, searxng_url: url.trim(), searxng_auth_header: header.trim() },
      ...(key && engine ? { engine_keys: { [engine]: key } } : {}),
    });
    if (ok) setKey("");
  };

  return (
    <Group title={t("settings.admin.engineTitle")} description={t("settings.admin.engineHelp")}>
      <Field label={t("settings.admin.engine")} htmlFor="web-engine">
        <Select id="web-engine" value={engine} onChange={(e) => setEngine(e.target.value as WebEngineKind | "")} className="max-w-sm">
          <option value="">{t("settings.admin.engineNone")}</option>
          {ENGINES.map((k) => (
            <option key={k} value={k}>
              {t(`settings.admin.names.${k}`)}
            </option>
          ))}
        </Select>
      </Field>
      {engine === "searxng" && (
        <>
          <Field label={t("settings.admin.searxngUrl")} htmlFor="web-searxng" hint={t("settings.admin.searxngUrlHelp")}>
            <Input id="web-searxng" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://searxng:8080" /* i18n-ignore: example address */ className="font-mono" autoComplete="off" />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("settings.admin.authHeader")} htmlFor="web-auth-header" hint={t("settings.admin.authHeaderHelp")}>
              <Input id="web-auth-header" value={header} onChange={(e) => setHeader(e.target.value)} className="font-mono" autoComplete="off" />
            </Field>
            {header && <KeyField id="web-auth-value" label={t("settings.admin.authValue")} hasKey={!!info?.has_key} value={key} onChange={setKey} onRemove={() => save({ engine_keys: { searxng: null } })} />}
          </div>
        </>
      )}
      {(engine === "brave" || engine === "tavily") && (
        <KeyField id="web-api-key" label={t("settings.admin.apiKey")} hasKey={!!info?.has_key} value={key} onChange={setKey} onRemove={() => save({ engine_keys: { [engine]: null } })} />
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={submit}>
          {t("settings.admin.saveEngine")}
        </Button>
        {engine && (
          <Button variant="outline" icon={testing ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />} onClick={test} disabled={testing}>
            {t("settings.admin.test")}
          </Button>
        )}
      </div>
      {engine && (
        <p className="flex items-start gap-2 text-[0.8125rem] text-muted" role="status" aria-live="polite" data-testid="engine-status">
          {last ? (
            last.ok && !last.error_code ? (
              <>
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
                <span>
                  {t("settings.admin.testOk", { count: last.result_count, ms: Math.round(last.latency_ms ?? 0) })}
                  {last.checked_at && <span className="text-faint"> · {t("settings.admin.lastTest", { when: relativeTime(last.checked_at) })}</span>}
                </span>
              </>
            ) : (
              <>
                <XCircle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
                <span>
                  {t("settings.admin.testFailed", { reason: providerErrorText(last.error_code, last.error_params, last.error_code ?? "") })}
                  {last.checked_at && <span className="text-faint"> · {t("settings.admin.lastTest", { when: relativeTime(last.checked_at) })}</span>}
                </span>
              </>
            )
          ) : (
            <span>{t("settings.admin.notTested")}</span>
          )}
        </p>
      )}
    </Group>
  );
}

function KeyField({ id, label, hasKey, value, onChange, onRemove }: { id: string; label: string; hasKey: boolean; value: string; onChange: (v: string) => void; onRemove: () => void }) {
  const { t } = useTranslation("web");
  return (
    <Field label={label} htmlFor={id} hint={hasKey ? t("settings.admin.keySaved") : t("settings.admin.keyMissing")}>
      <div className="flex gap-2">
        <Input id={id} type="password" value={value} onChange={(e) => onChange(e.target.value)} autoComplete="new-password" className="font-mono" />
        {hasKey && (
          <Button variant="ghost" onClick={onRemove}>
            {t("settings.admin.removeKey")}
          </Button>
        )}
      </div>
    </Field>
  );
}

function PromptField({ id, label, value, fallback, onSave }: { id: string; label: string; value: string; fallback: string; onSave: (v: string) => void }) {
  const { t } = useTranslation(["web", "common"]);
  const [text, setText] = useState(value || fallback);
  useEffect(() => setText(value || fallback), [value, fallback]);
  const changed = text.trim() !== (value || fallback).trim();
  return (
    <Field label={label} htmlFor={id}>
      <Textarea id={id} rows={8} value={text} onChange={(e) => setText(e.target.value)} className="font-mono text-[0.78rem]" />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="primary" disabled={!changed} onClick={() => onSave(text.trim() === fallback.trim() ? "" : text)}>
          {t("common:save")}
        </Button>
        <Button size="sm" variant="ghost" disabled={!value} onClick={() => onSave("")}>
          {t("settings.admin.resetDefault")}
        </Button>
      </div>
    </Field>
  );
}

function PipelineSettings({ settings, defaults, save }: { settings: WebAdminSettings; defaults: WebAdminView["defaults"]; save: Save }) {
  const { t } = useTranslation("web");
  return (
    <Group title={t("settings.admin.pipelineTitle")} description={t("settings.admin.promptHelp")}>
      <Switch
        label={t("settings.admin.queryGeneration")}
        description={t("settings.admin.queryGenerationHelp")}
        checked={settings.query_generation}
        onChange={(v) => save({ settings: { query_generation: v } })}
      />
      <PromptField id="web-query-prompt" label={t("settings.admin.queryPrompt")} value={settings.query_prompt} fallback={defaults.query_prompt} onSave={(v) => save({ settings: { query_prompt: v } })} />
      <PromptField
        id="web-grounding-prompt"
        label={t("settings.admin.groundingPrompt")}
        value={settings.grounding_prompt}
        fallback={defaults.grounding_prompt}
        onSave={(v) => save({ settings: { grounding_prompt: v } })}
      />
    </Group>
  );
}

function ListField({ id, label, hint, value, onSave }: { id: string; label: string; hint: string; value: string[]; onSave: (v: string[]) => void }) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => setText(value.join("\n")), [value]);
  return (
    <Field label={label} htmlFor={id} hint={hint}>
      <Textarea id={id} rows={3} value={text} onChange={(e) => setText(e.target.value)} onBlur={() => text !== value.join("\n") && onSave(lines(text))} className="font-mono" />
    </Field>
  );
}

function NetworkSettings({ settings, save }: { settings: WebAdminSettings; save: Save }) {
  const { t } = useTranslation("web");
  const [ports, setPorts] = useState(settings.allowed_ports.join(", "));
  const [ua, setUa] = useState(settings.user_agent);
  useEffect(() => {
    setPorts(settings.allowed_ports.join(", "));
    setUa(settings.user_agent);
  }, [settings.allowed_ports, settings.user_agent]);
  return (
    <Group title={t("settings.admin.networkTitle")}>
      <ListField id="web-allowlist" label={t("settings.admin.allowlist")} hint={t("settings.admin.allowlistHelp")} value={settings.fetch_allowlist} onSave={(v) => save({ settings: { fetch_allowlist: v } })} />
      <div className="grid gap-4 sm:grid-cols-2">
        <ListField id="web-deny" label={t("settings.admin.denylist")} hint={t("settings.admin.sitesHelp")} value={settings.domain_denylist} onSave={(v) => save({ settings: { domain_denylist: v } })} />
        <ListField id="web-allow" label={t("settings.admin.allowSites")} hint={t("settings.admin.sitesHelp")} value={settings.domain_allowlist} onSave={(v) => save({ settings: { domain_allowlist: v } })} />
      </div>
      <Field label={t("settings.admin.ports")} htmlFor="web-ports" hint={t("settings.admin.portsHelp")}>
        <Input
          id="web-ports"
          value={ports}
          onChange={(e) => setPorts(e.target.value)}
          onBlur={() => {
            const parsed = ports.split(",").map((p) => Number(p.trim())).filter((n) => Number.isInteger(n) && n > 0 && n < 65536);
            if (parsed.join(", ") !== settings.allowed_ports.join(", ") && parsed.length) void save({ settings: { allowed_ports: parsed } });
          }}
          className="max-w-xs font-mono"
        />
      </Field>
      <Switch label={t("settings.admin.robots")} description={t("settings.admin.robotsHelp")} checked={settings.robots_for_search} onChange={(v) => save({ settings: { robots_for_search: v } })} />
      <Field label={t("settings.admin.userAgent")} htmlFor="web-ua" hint={t("settings.admin.userAgentHelp")}>
        <Input id="web-ua" value={ua} onChange={(e) => setUa(e.target.value)} onBlur={() => ua.trim() && ua !== settings.user_agent && save({ settings: { user_agent: ua.trim() } })} className="font-mono" />
      </Field>
    </Group>
  );
}

const LIMITS = [
  ["max_urls_per_message", "maxUrls", 1, 10],
  ["max_results", "maxResults", 1, 10],
  ["per_source_chars", "perSource", 1000, 200000],
  ["total_chars", "total", 2000, 800000],
  ["max_page_kb", "maxPage", 64, 51200],
  ["timeout_seconds", "timeout", 2, 120],
  ["fetches_per_hour", "fetchesPerHour", 1, 100000],
  ["searches_per_hour", "searchesPerHour", 1, 100000],
] as const;

function LimitSettings({ settings, save }: { settings: WebAdminSettings; save: Save }) {
  const { t } = useTranslation("web");
  const [values, setValues] = useState(() => Object.fromEntries(LIMITS.map(([k]) => [k, settings[k]])) as Record<(typeof LIMITS)[number][0], number>);
  useEffect(() => setValues(Object.fromEntries(LIMITS.map(([k]) => [k, settings[k]])) as typeof values), [settings]);
  return (
    <Group title={t("settings.admin.limitsTitle")}>
      <div className="grid gap-4 sm:grid-cols-2">
        {LIMITS.map(([key, label, min, max]) => (
          <Field key={key} label={t(`settings.admin.${label}`)} htmlFor={`web-${key}`}>
            <Input
              id={`web-${key}`}
              type="number"
              min={min}
              max={max}
              value={values[key]}
              onChange={(e) => setValues((v) => ({ ...v, [key]: Number(e.target.value) }))}
              onBlur={() => {
                const v = Math.min(max, Math.max(min, values[key] || min));
                if (v !== settings[key]) void save({ settings: { [key]: v } }, true);
              }}
            />
          </Field>
        ))}
      </div>
    </Group>
  );
}
