/** Web reading & search in messages: progress steps, sources, link status. */
import { AlertTriangle, AppWindow, Check, ChevronRight, ExternalLink, FileText, Globe, Link2, RotateCw, Search, Sparkles } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { errorText, providerErrorText } from "@/lib/errors";
import { formatDateTime, relativeTime } from "@/lib/time";
import type { Message, WebRef, WebSource, WebStep } from "@/lib/types";
import { displayDomain } from "@/lib/urls";
import { cn, formatNumber } from "@/lib/utils";
import { useChat } from "@/stores/chat";
import { useWeb } from "@/stores/web";
import { toast } from "@/stores/ui";
import type { CitationTarget } from "./Markdown";
import { Markdown } from "./Markdown";
import { Button, Dialog, Spinner, Tooltip } from "./ui";
import { ThinkingIndicator } from "./ThinkingIndicator";

export type WebSources = Record<string, WebSource>;

/** Citation map for an answer: only sources that were actually given to the model. */
export function citationTargets(message: Message, sources: WebSources): Record<number, CitationTarget> {
  const out: Record<number, CitationTarget> = {};
  for (const ref of message.web?.refs ?? []) {
    const s = sources[ref.source_id];
    if (!s || !ref.included) continue;
    const url = s.final_url || s.url;
    out[ref.n] = { url, title: s.title || displayDomain(url), domain: s.domain };
  }
  return out;
}

/** Sources listed under an answer: everything from this turn, plus earlier
 * pages the answer cites (same rule as ``ref_shown`` in web/pipeline.py). */
export function visibleRefs(message: Message): WebRef[] {
  const refs = message.web?.refs ?? [];
  const cited = new Set(message.web?.citations?.used ?? []);
  return refs.filter((r) => r.shown ?? (!r.earlier || cited.has(r.n)));
}

function sourceReason(s: WebSource): string {
  return providerErrorText(s.error_code, s.error_params, s.error_code ?? "");
}

/** Why the headless browser didn't help with a page that needs JavaScript (if it was tried). */
function browserReason(s: WebSource): string | null {
  const code = s.error_params?.browser;
  return typeof code === "string" ? providerErrorText(code, {}, code) : null;
}

/* -------------------------------------------------------------- steps */
function StepIcon({ step }: { step: WebStep }) {
  if (step.status === "running") return <ThinkingIndicator className="text-[0.875rem]" />;
  if (step.status === "error") return <AlertTriangle className="size-3.5 text-danger" aria-hidden />;
  if (step.status === "cancelled") return <span className="inline-block size-1.5 rounded-full bg-faint" aria-hidden />;
  const Icon = step.kind === "queries" ? Sparkles : step.kind === "search" ? Search : step.kind === "read_links" ? Link2 : step.kind === "read_results" ? Globe : Check;
  return <Icon className="size-3.5 text-faint" aria-hidden />;
}

function useStepText() {
  const { t } = useTranslation(["web", "errors"]);
  return (step: WebStep): string => {
    const p = step.params;
    const running = step.status === "running";
    if (step.status === "error" && step.code) return providerErrorText(step.code, {}, step.code);
    switch (step.kind) {
      case "read_links": {
        if (running) return t("steps.readLinks", { count: p.total ?? 0 });
        const read = t("steps.readLinksDone", { count: (p.done ?? 0) - (p.failed ?? 0) });
        return p.failed ? `${read} · ${t("steps.notRead", { count: p.failed })}` : read;
      }
      case "queries":
        if (running) return t("steps.queriesRunning");
        return p.fallback ? t("steps.queriesFallback") : t("steps.queries", { queries: (p.queries ?? []).join(" · ") });
      case "search":
        return running ? t("steps.searchRunning") : t("steps.found", { count: p.count ?? 0 });
      case "read_results": {
        if (running) return t("steps.readResults", { count: p.total ?? 0 });
        const read = t("steps.readResultsDone", { count: (p.done ?? 0) - (p.failed ?? 0) });
        return p.failed ? `${read} · ${t("steps.summaryOnly", { count: p.failed })}` : read;
      }
      case "budget":
        return t("steps.budget", { used: p.used ?? 0, count: p.total ?? 0 });
      case "older_omitted":
        return t("steps.olderOmitted", { count: p.count ?? 0 });
      case "history_trimmed":
        return t("steps.historyTrimmed", { count: p.count ?? 0 });
      default:
        return "";
    }
  };
}

export function WebSteps({ message, sources, defaultOpen }: { message: Message; sources: WebSources; defaultOpen: boolean }) {
  const { t } = useTranslation("web");
  const text = useStepText();
  const steps = message.web?.steps ?? [];
  const running = steps.some((s) => s.status === "running");
  const [open, setOpen] = useState(defaultOpen);
  const hintId = useId();
  if (!steps.length) return null;
  const expanded = running || open;
  const current = steps.findLast((s) => s.status === "running");
  // Only this turn's sources: pages re-sent from earlier turns aren't "read" again.
  const included = (message.web?.refs ?? []).filter((r) => !r.earlier && r.included && sources[r.source_id]?.status !== "error").length;
  const summary = running
    ? text(current ?? steps[steps.length - 1])
    : steps.some((s) => s.status === "error")
      ? t("steps.failed")
      : steps.some((s) => s.status === "cancelled")
        ? t("steps.stopped")
        : message.web?.search
          ? t("steps.summarySources", { count: included })
          : t("steps.summaryLinks", { count: included });

  return (
    <div className="mb-3" data-testid="web-steps">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={expanded}
        aria-describedby={hintId}
        disabled={running}
        className="flex max-w-full items-center gap-1.5 rounded-md py-0.5 text-left text-[0.8125rem] text-muted transition-colors hover:text-fg disabled:hover:text-muted"
      >
        {running ? <ThinkingIndicator className="text-[0.875rem]" /> : <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", expanded && "rotate-90")} aria-hidden />}
        <span className="truncate" aria-live="polite">{summary}</span>
      </button>
      <span id={hintId} hidden>
        {t("steps.toggle")}
      </span>
      {expanded && (
        <ol className="mt-1.5 ml-1.5 space-y-1.5 border-l-2 border-line pl-4" aria-label={t("steps.label")}>
          {steps.map((step) => (
            <li key={step.id} className="text-[0.8125rem] leading-relaxed text-muted">
              <span className="flex items-start gap-2">
                <span className="mt-[0.3rem] flex size-3.5 shrink-0 items-center justify-center">
                  <StepIcon step={step} />
                </span>
                <span className={cn("min-w-0 break-words", step.status === "error" && "text-danger")}>
                  {text(step)}
                  {step.status === "cancelled" && <span className="text-faint"> · {t("steps.stopped")}</span>}
                </span>
              </span>
              {!!step.params.domains?.length && (
                <span className="mt-1 ml-5.5 flex flex-wrap gap-1">
                  {[...new Set(step.params.domains)].map((d) => (
                    <span key={d} className="rounded-md bg-subtle px-1.5 py-px font-mono text-[0.68rem] text-muted">
                      {d}
                    </span>
                  ))}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ sources */
export function WebSourceList({ refs, sources, conversationId }: { refs: WebRef[]; sources: WebSources; conversationId: string }) {
  const { t } = useTranslation("web");
  const [active, setActive] = useState<WebSource | null>(null);
  const items = refs.map((r) => ({ ref: r, source: sources[r.source_id] })).filter((i) => i.source);
  if (!items.length) return null;
  return (
    <div className="mt-3" data-testid="web-sources">
      <p className="mb-1.5 text-[0.72rem] font-medium text-faint" id={`sources-${refs[0].source_id}`}>
        {t("sources.title")}
      </p>
      <ol className="grid gap-1 sm:grid-cols-2" aria-labelledby={`sources-${refs[0].source_id}`}>
        {items.map(({ ref, source }) => {
          const url = source.final_url || source.url;
          // A failure always shows its own reason; "left out" is only for pages
          // that were read but didn't fit the model's context.
          const state = source.status === "error" ? "error" : !ref.included ? "excluded" : source.status;
          const reason = state === "error" ? sourceReason(source) : null;
          return (
            <li key={ref.n} className="group/src flex min-w-0 items-center gap-1 rounded-lg border border-line pr-1 transition-colors hover:bg-hover">
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer nofollow"
                className="flex min-w-0 flex-1 items-center gap-2 py-1.5 pl-2"
                title={url}
              >
                <span className={cn("w-5 shrink-0 text-center font-mono text-[0.72rem]", state === "ok" || state === "snippet" ? "text-accent-text" : "text-faint")}>{ref.n}</span>
                <span className="min-w-0">
                  <span className={cn("block truncate text-[0.8125rem]", state === "ok" ? "text-fg" : "text-muted")}>{source.title || source.domain}</span>
                  <span className="flex items-center gap-1.5 text-[0.7rem] text-faint">
                    <span className={cn("truncate", reason && "max-w-[45%] shrink-0")}>{source.domain}</span>
                    {state === "snippet" && <span className="shrink-0 text-warning">· {t("sources.status.snippet")}</span>}
                    {reason && (
                      <span className="min-w-0 truncate text-danger" title={reason} data-testid="source-reason">
                        · {reason}
                      </span>
                    )}
                    {state === "excluded" && <span className="shrink-0">· {t("sources.notIncluded")}</span>}
                  </span>
                </span>
              </a>
              <Tooltip label={t("sources.viewText")}>
                <button
                  type="button"
                  onClick={() => setActive(source)}
                  aria-label={`${t("sources.viewText")}: ${source.title || source.domain}`}
                  className="flex size-7 shrink-0 items-center justify-center rounded-md text-faint opacity-70 transition-opacity group-hover/src:opacity-100 hover:bg-subtle hover:text-fg focus-visible:opacity-100"
                >
                  <FileText className="size-3.5" />
                </button>
              </Tooltip>
            </li>
          );
        })}
      </ol>
      <SourceDialog source={active} onClose={() => setActive(null)} conversationId={conversationId} />
    </div>
  );
}

/** Status of the links read for a user message (shown under the bubble). */
export function LinkStatus({ sources, conversationId }: { sources: WebSource[]; conversationId: string }) {
  const { t } = useTranslation("web");
  const [active, setActive] = useState<WebSource | null>(null);
  if (!sources.length) return null;
  return (
    <>
      <ul className="mt-1.5 flex max-w-[85%] flex-wrap justify-end gap-1.5" aria-label={t("composer.linksLabel")} data-testid="link-status">
        {sources.map((s) => {
          const ok = s.status === "ok";
          const label = ok ? s.title || s.domain : t("sources.failedTo", { domain: s.domain, reason: sourceReason(s) });
          return (
            <li key={s.id}>
              <Tooltip label={label}>
                <button
                  type="button"
                  onClick={() => setActive(s)}
                  className={cn(
                    "flex h-7 max-w-[16rem] items-center gap-1.5 rounded-lg border px-2 text-[0.75rem] transition-colors hover:bg-hover",
                    ok ? "border-line text-muted" : "border-danger/30 text-danger",
                  )}
                  aria-label={ok ? `${t("sources.status.ok")}: ${label}` : label}
                >
                  {ok ? <Check className="size-3.5 shrink-0 text-accent" aria-hidden /> : <AlertTriangle className="size-3.5 shrink-0" aria-hidden />}
                  <span className="truncate">{s.domain}</span>
                </button>
              </Tooltip>
            </li>
          );
        })}
      </ul>
      <SourceDialog source={active} onClose={() => setActive(null)} conversationId={conversationId} />
    </>
  );
}

function SourceDialog({ source, onClose, conversationId }: { source: WebSource | null; onClose: () => void; conversationId: string }) {
  const { t } = useTranslation(["web", "common"]);
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "auto" | "browser">(null);
  const browserAvailable = useWeb((st) => !!st.status?.browser_available);
  const live = useChat((s) => (source ? s.convs[conversationId]?.webSources[source.id] : undefined)) ?? source;

  useEffect(() => {
    setContent(null);
    setError(null);
    if (!source) return;
    api
      .webSource(source.id)
      .then((r) => setContent(r.content))
      .catch((e) => setError(errorText(e)));
  }, [source]);

  const refetch = async (loader: "auto" | "browser") => {
    if (!source) return;
    setBusy(loader);
    try {
      const r = await api.refetchWebSource(source.id, loader);
      useChat.getState().updateWebSource(conversationId, r.source);
      if (r.ok) {
        const fresh = await api.webSource(source.id);
        setContent(fresh.content);
        toast(t("sources.refetched"), "success");
      } else {
        toast(t("sources.refetchFailed", { reason: providerErrorText(r.error_code, r.error_params ?? {}, r.error_code ?? "") }), "error");
      }
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setBusy(null);
    }
  };

  const s = live;
  const url = s ? s.final_url || s.url : "";
  return (
    <Dialog
      open={!!source}
      onOpenChange={(o) => !o && onClose()}
      title={s ? s.title || s.domain : ""}
      description={s ? t("sources.extractedHelp") : undefined}
      size="lg"
      footer={
        s && (
          <>
            <a href={url} target="_blank" rel="noopener noreferrer nofollow" className="mr-auto inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[0.8125rem] text-muted hover:bg-hover hover:text-fg">
              <ExternalLink className="size-3.5" /> {t("sources.open")}
            </a>
            {browserAvailable && (
              <Button variant="outline" icon={<AppWindow className="size-3.5" />} loading={busy === "browser"} disabled={!!busy} onClick={() => refetch("browser")}>
                {t("sources.renderBrowser")}
              </Button>
            )}
            <Button variant="outline" icon={<RotateCw className="size-3.5" />} loading={busy === "auto"} disabled={!!busy} onClick={() => refetch("auto")}>
              {t("sources.refetch")}
            </Button>
          </>
        )
      }
    >
      {s && (
        <div className="pb-3">
          <p className="mb-2 truncate font-mono text-[0.72rem] text-faint" title={url}>{url}</p>
          <p className="mb-3 flex flex-wrap gap-x-3 gap-y-1 text-[0.75rem] text-muted">
            <span>{s.origin === "search" ? t("sources.fromSearch") : t("sources.pasted")}</span>
            {s.fetched_at && <span title={formatDateTime(s.fetched_at)}>{t("sources.retrieved", { when: relativeTime(s.fetched_at) })}</span>}
            {s.published && <span>{t("sources.published", { date: s.published })}</span>}
            {s.chars > 0 && <span>{t("sources.characters", { chars: formatNumber(s.chars) })}</span>}
            {s.loader === "browser" && <span>{t("sources.renderedWithBrowser")}</span>}
          </p>
          {s.status === "error" && (
            <p role="alert" className="mb-3 rounded-lg border border-danger/25 bg-danger-soft px-3 py-2 text-[0.8125rem] text-fg">
              {t("sources.failedTo", { domain: s.domain, reason: sourceReason(s) })}
              {browserReason(s) && <> {t("sources.browserFailed", { reason: browserReason(s) })}</>}
              {!browserReason(s) && s.error_code === "web.needs_javascript" && browserAvailable && <> {t("sources.tryBrowser")}</>}
            </p>
          )}
          {s.status === "snippet" && <p className="mb-3 text-[0.8125rem] text-muted">{t("sources.snippetOnly")}</p>}
          {s.notes.includes("transcript_unavailable") && <p className="mb-3 text-[0.8125rem] text-muted">{t("sources.transcript")}</p>}
          {s.truncated && <p className="mb-3 text-[0.8125rem] text-muted">{t("sources.truncated")}</p>}
          {error ? (
            <p className="text-sm text-danger">{error}</p>
          ) : content === null ? (
            <Spinner />
          ) : content ? (
            <div className="max-h-[55vh] overflow-y-auto rounded-lg border border-line bg-bg px-4 py-3">
              <Markdown content={content} className="text-[0.875rem]" />
            </div>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}
