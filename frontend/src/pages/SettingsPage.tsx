import { Download, HardDrive, Monitor, Moon, Sun, Upload } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, useParams } from "react-router-dom";
import { LanguageSelect } from "@/components/LanguageSelect";
import { Group, PageHeader, PageShell } from "@/components/Page";
import { ParamsEditor, cleanParams } from "@/components/ParamsEditor";
import { Button, ConfirmDialog, Field, Input, SegmentedControl, Select, Spinner, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText, providerErrorText } from "@/lib/errors";
import { relativeTime } from "@/lib/time";
import type { AppSettings, GenerationParams, UpdateView } from "@/lib/types";
import { UPDATE_DOCS } from "@/components/UpdateNotice";
import { cn, downloadUrl, formatBytes } from "@/lib/utils";
import { AccountSettings } from "@/pages/settings/AccountSettings";
import { AdminUsers } from "@/pages/settings/AdminUsers";
import { ContextSettingsGroup } from "@/pages/settings/ContextSettings";
import { WebSettings } from "@/pages/settings/WebSettings";
import { useAuth } from "@/stores/auth";
import { useConversations } from "@/stores/conversations";
import { findProvider, useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";

/** SPDX identifier (not translated). */
const LICENSE_SPDX = "MIT";

const SECTIONS = ["account", "appearance", "chat", "web", "documents", "data", "users", "about"] as const;
type SectionId = (typeof SECTIONS)[number];

export function SettingsPage() {
  const { t } = useTranslation("settings");
  const { section = "account" } = useParams();
  const settings = useSettings((s) => s.settings);
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const visible = SECTIONS.filter((s) => s !== "users" || isAdmin);
  const current = (visible as readonly string[]).includes(section) ? (section as SectionId) : "account";

  useEffect(() => {
    document.title = `${t("title")} · Chimera`;
  }, [t]);

  return (
    <PageShell wide>
      <PageHeader title={t("title")} />
      <div className="flex flex-col gap-8 lg:flex-row lg:gap-12">
        <nav aria-label={t("sections")} className="-mx-1 flex shrink-0 gap-1 overflow-x-auto px-1 lg:w-48 lg:flex-col">
          {visible.map((s) => (
            <NavLink
              key={s}
              to={`/settings/${s}`}
              className={({ isActive }) =>
                cn(
                  "flex h-8 shrink-0 items-center rounded-lg px-3 text-[0.8125rem] whitespace-nowrap transition-colors",
                  isActive || current === s ? "bg-active text-fg" : "text-muted hover:bg-hover hover:text-fg",
                )
              }
            >
              {t(`nav.${s}`)}
            </NavLink>
          ))}
          <Link to="/models" className="flex h-8 shrink-0 items-center rounded-lg px-3 text-[0.8125rem] whitespace-nowrap text-muted transition-colors hover:bg-hover hover:text-fg">
            {t("nav.models")} →
          </Link>
        </nav>
        <div className="min-w-0 flex-1 lg:max-w-2xl">
          {current === "account" ? (
            <AccountSettings />
          ) : current === "users" ? (
            <AdminUsers />
          ) : current === "about" ? (
            <About />
          ) : current === "data" ? (
            <DataSettings />
          ) : !settings ? (
            <Spinner />
          ) : current === "appearance" ? (
            <Appearance settings={settings} />
          ) : current === "chat" ? (
            <ChatSettings settings={settings} />
          ) : current === "web" ? (
            <WebSettings settings={settings} />
          ) : (
            <DocumentSettings settings={settings} />
          )}
        </div>
      </div>
    </PageShell>
  );
}

function useSave() {
  const update = useSettings((s) => s.update);
  return (patch: Partial<AppSettings>) => update(patch).catch((e) => toast(errorText(e), "error"));
}

function Row({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-fg">{label}</p>
        {description && <p className="text-[0.8125rem] text-muted">{description}</p>}
      </div>
      {children}
    </div>
  );
}

function Appearance({ settings }: { settings: AppSettings }) {
  const { t } = useTranslation("settings");
  const save = useSave();
  return (
    <>
      <Group title={t("appearance.language")} description={t("appearance.languageHelp")}>
        <LanguageSelect className="-ml-1.5" compact />
      </Group>
      <Group title={t("appearance.theme")}>
        <SegmentedControl
          label={t("appearance.theme")}
          value={settings.theme}
          onChange={(theme) => save({ theme })}
          options={[
            { value: "system", label: <><Monitor className="size-3.5" /> {t("appearance.system")}</> },
            { value: "light", label: <><Sun className="size-3.5" /> {t("appearance.light")}</> },
            { value: "dark", label: <><Moon className="size-3.5" /> {t("appearance.dark")}</> },
          ]}
        />
      </Group>
      <Group title={t("appearance.reading")}>
        <Row label={t("appearance.fontSize")}>
          <SegmentedControl
            label={t("appearance.fontSize")}
            value={settings.font_size}
            onChange={(font_size) => save({ font_size })}
            options={[
              { value: "sm", label: t("appearance.small") },
              { value: "md", label: t("appearance.default") },
              { value: "lg", label: t("appearance.large") },
            ]}
          />
        </Row>
        <Row label={t("appearance.width")}>
          <SegmentedControl
            label={t("appearance.width")}
            value={settings.chat_width}
            onChange={(chat_width) => save({ chat_width })}
            options={[
              { value: "narrow", label: t("appearance.narrow") },
              { value: "default", label: t("appearance.default") },
              { value: "wide", label: t("appearance.wide") },
            ]}
          />
        </Row>
      </Group>
      <Group title={t("appearance.interface")}>
        <Switch label={t("appearance.collapse")} description={t("appearance.collapseHelp")} checked={settings.sidebar_collapsed} onChange={(v) => save({ sidebar_collapsed: v })} />
        <Switch label={t("appearance.reduceMotion")} description={t("appearance.reduceMotionHelp")} checked={settings.reduce_motion} onChange={(v) => save({ reduce_motion: v })} />
      </Group>
    </>
  );
}

function ChatSettings({ settings }: { settings: AppSettings }) {
  const { t } = useTranslation("settings");
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
      <Group title={t("chat.behavior")}>
        <Switch label={t("chat.enterToSend")} description={t("chat.enterToSendHelp")} checked={settings.enter_to_send} onChange={(v) => save({ enter_to_send: v })} />
        <Switch label={t("chat.stream")} description={t("chat.streamHelp")} checked={settings.stream} onChange={(v) => save({ stream: v })} />
        <Switch label={t("chat.autoTitle")} description={t("chat.autoTitleHelp")} checked={settings.auto_title} onChange={(v) => save({ auto_title: v })} />
        <Switch label={t("chat.showReasoning")} description={t("chat.showReasoningHelp")} checked={settings.show_reasoning} onChange={(v) => save({ show_reasoning: v })} />
      </Group>
      <Group title={t("chat.systemPrompt")} description={t("chat.systemPromptHelp")}>
        <Textarea
          rows={6}
          value={prompt}
          onChange={(e) => {
            setPrompt(e.target.value);
            setDirty(true);
          }}
          placeholder={t("chat.systemPromptPlaceholder")}
          aria-label={t("chat.systemPrompt")}
        />
      </Group>
      <Group title={t("chat.params")} description={provider ? t("chat.paramsHelpProvider", { name: provider.name }) : t("chat.paramsHelp")}>
        <ParamsEditor
          value={params}
          onChange={(p) => {
            setParams(p);
            setDirty(true);
          }}
          supported={provider?.supported_params}
        />
      </Group>
      <ContextSettingsGroup settings={settings} />
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
            toast(t("chat.saved"), "success");
          }}
        >
          {t("saveChanges")}
        </Button>
      </div>
    </>
  );
}

function DocumentSettings({ settings }: { settings: AppSettings }) {
  const { t } = useTranslation("settings");
  const save = useSave();
  const entries = useModels((s) => s.entries);
  const k = settings.knowledge;
  const [mb, setMb] = useState(settings.max_attachment_mb);
  const [chars, setChars] = useState(settings.max_attachment_chars);
  const [embedModel, setEmbedModel] = useState(k.embedding_model ?? "");
  const [chunk, setChunk] = useState({ chunk_size: k.chunk_size, chunk_overlap: k.chunk_overlap, top_k: k.top_k });
  const fields = [
    ["chunk_size", t("documents.chunkSize"), 200, 8000],
    ["chunk_overlap", t("documents.chunkOverlap"), 0, 2000],
    ["top_k", t("documents.topK"), 1, 20],
  ] as const;

  return (
    <>
      <Group title={t("documents.attachments")} description={t("documents.attachmentsHelp")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("documents.maxSize")} htmlFor="att-mb">
            <Input id="att-mb" type="number" min={1} max={100} value={mb} onChange={(e) => setMb(Number(e.target.value))} onBlur={() => mb !== settings.max_attachment_mb && save({ max_attachment_mb: Math.min(100, Math.max(1, mb || 10)) })} />
          </Field>
          <Field label={t("documents.maxChars")} htmlFor="att-chars" hint={t("documents.maxCharsHelp")}>
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
      <Group title={t("documents.knowledge")} description={t("documents.knowledgeHelp")}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("documents.embeddingConnection")} htmlFor="kb-provider">
            <Select
              id="kb-provider"
              value={k.embedding_provider_id ?? ""}
              onChange={(e) => save({ knowledge: { ...k, embedding_provider_id: e.target.value || null, embedding_model: e.target.value ? k.embedding_model : null } })}
            >
              <option value="">{t("documents.none")}</option>
              {entries.map((e) => (
                <option key={e.provider.id} value={e.provider.id}>
                  {e.provider.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("documents.embeddingModel")} htmlFor="kb-model" hint={t("documents.embeddingModelHelp")}>
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
          {fields.map(([key, label, min, max]) => (
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
  const { t } = useTranslation("settings");
  const isAdmin = useAuth((s) => s.user?.role === "admin");
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
      <Group title={t("data.private")}>
        <ul className="space-y-2 text-[0.8125rem] leading-relaxed text-muted">
          <li>• {t("data.point1")}</li>
          <li>• {t("data.point2")}</li>
          <li>• {t("data.point3")}</li>
        </ul>
        {info && isAdmin && info.database && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 rounded-xl bg-subtle px-4 py-3 text-[0.8125rem]">
            <dt className="text-muted">{t("data.database")}</dt>
            <dd className="truncate font-mono text-fg" title={info.database}>
              {info.database}
            </dd>
            <dt className="text-muted">{t("data.size")}</dt>
            <dd className="text-fg">{formatBytes(info.db_size ?? 0)}</dd>
            <dt className="text-muted">{t("data.accounts")}</dt>
            <dd className="text-fg">{info.users}</dd>
            <dt className="text-muted">{t("data.telemetry")}</dt>
            <dd className="text-fg">{t("data.off")}</dd>
          </dl>
        )}
      </Group>
      <Group title={t("data.exportTitle")} description={t("data.exportHelp")}>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" icon={<Download className="size-4" />} onClick={() => downloadUrl("/api/data/export")}>
            {t("data.export")}
          </Button>
          <Button variant="outline" icon={<Upload className="size-4" />} onClick={() => importRef.current?.click()}>
            {t("data.import")}
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
                toast(
                  t("data.imported", {
                    conversations: t("data.conversationCount", { count: r.conversations }),
                    prompts: t("data.promptCount", { count: r.prompts }),
                  }) + (r.skipped ? ` ${t("data.skipped", { count: r.skipped })}` : ""),
                  "success",
                );
                await loadConversations();
              } catch (err) {
                toast(errorText(err), "error");
              }
            }}
          />
        </div>
      </Group>
      {isAdmin && (
        <Group title={t("data.backupTitle")} description={t("data.backupHelp")}>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" icon={<HardDrive className="size-4" />} onClick={() => downloadUrl("/api/data/backup")}>
              {t("data.backup")}
            </Button>
            <Button variant="outline" icon={<Upload className="size-4" />} onClick={() => restoreRef.current?.click()}>
              {t("data.restore")}
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
      )}
      <Group title={t("data.deleteTitle")} description={t("data.deleteHelp")}>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" className="text-danger" onClick={() => setConfirm("conversations")}>
            {t("data.deleteConversations")}
          </Button>
          <Button variant="outline" className="text-danger" onClick={() => setConfirm("all")}>
            {t("data.deleteAll")}
          </Button>
        </div>
      </Group>
      <ConfirmDialog
        open={confirm === "conversations" || confirm === "all"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={confirm === "all" ? t("data.deleteAllTitle") : t("data.deleteConversationsTitle")}
        description={confirm === "all" ? t("data.deleteAllBody") : t("data.deleteConversationsBody")}
        confirmLabel={confirm === "all" ? t("data.deleteAllConfirm") : t("data.deleteConversationsConfirm")}
        onConfirm={async () => {
          try {
            await api.clearData(confirm === "all" ? "all" : "conversations");
            await reloadAll();
            toast(t("data.deleted"));
          } catch (err) {
            toast(errorText(err), "error");
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
        title={t("data.restoreTitle")}
        description={t("data.restoreBody", { name: pendingRestore?.name ?? "" })}
        confirmLabel={t("data.restoreConfirm")}
        onConfirm={async () => {
          if (!pendingRestore) return;
          try {
            await api.restore(pendingRestore);
            toast(t("data.restored"), "success");
            window.location.reload();
          } catch (err) {
            toast(errorText(err), "error");
          }
        }}
      />
    </>
  );
}

function About() {
  const { t } = useTranslation("settings");
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const [info, setInfo] = useState<{ version: string; commit: string | null } | null>(null);
  useEffect(() => {
    api.info().then(setInfo).catch(() => undefined);
  }, []);
  return (
    <>
      <Group title="Chimera" description={t("about.tagline")}>
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-[0.8125rem]">
          <dt className="text-muted">{t("about.version")}</dt>
          <dd className="font-mono text-fg" data-testid="about-version">{info?.version ?? "…"}</dd>
          <dt className="text-muted">{t("about.build")}</dt>
          <dd className="font-mono text-fg">{info ? (info.commit ?? t("about.buildLocal")) : "…"}</dd>
          <dt className="text-muted">{t("about.inference")}</dt>
          <dd className="text-fg">{t("about.inferenceValue")}</dd>
          <dt className="text-muted">{t("about.license")}</dt>
          <dd className="text-fg">{LICENSE_SPDX}</dd>
        </dl>
      </Group>
      {isAdmin && <UpdateSettings />}
    </>
  );
}

function UpdateSettings() {
  const { t } = useTranslation(["settings", "errors"]);
  const [view, setView] = useState<UpdateView | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.adminUpdates().then(setView).catch(() => undefined);
  }, []);
  if (!view) return null;
  const last = view.last_check;
  return (
    <Group title={t("updates.title")} description={t("updates.help")}>
      <Switch
        label={t("updates.check")}
        description={t("updates.checkHelp")}
        checked={view.enabled}
        disabled={busy}
        onChange={async (v) => {
          setBusy(true);
          try {
            setView(await api.setUpdateCheck(v));
          } catch (err) {
            toast(errorText(err), "error");
          } finally {
            setBusy(false);
          }
        }}
      />
      {view.available && (
        <p className="rounded-lg bg-subtle px-3 py-2 text-[0.8125rem] text-fg" data-testid="about-update">
          {t("updates.available", { version: view.available.version })}{" "}
          <a href={view.available.url} target="_blank" rel="noopener noreferrer" className="text-accent-text hover:underline">
            {t("updates.releaseNotes")}
          </a>
          {" · "}
          <a href={UPDATE_DOCS} target="_blank" rel="noopener noreferrer" className="text-accent-text hover:underline">
            {t("updates.howTo")}
          </a>
        </p>
      )}
      {view.enabled && (
        <p className="text-[0.75rem] text-faint" data-testid="update-last-check">
          {!last?.checked_at
            ? t("updates.neverChecked")
            : last.ok
              ? view.available
                ? t("updates.checkedAt", { when: relativeTime(last.checked_at) })
                : t("updates.upToDate", { when: relativeTime(last.checked_at) })
              : t("updates.checkFailed", { when: relativeTime(last.checked_at), reason: providerErrorText(last.error, {}, last.error ?? "") })}
        </p>
      )}
      <p className="text-[0.75rem] leading-relaxed text-faint">{t("updates.never")}</p>
    </Group>
  );
}
