import * as Popover from "@radix-ui/react-popover";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import type { ContextEstimate } from "@/lib/types";
import { cn, compactNumber, formatNumber } from "@/lib/utils";
import { pathMessages, useChat } from "@/stores/chat";
import { useUI } from "@/stores/ui";

const CHARS_PER_TOKEN = 3.5;
type Part = "system" | "summary" | "latest" | "pinned" | "history" | "reserved";
const PARTS: Part[] = ["system", "summary", "pinned", "history", "latest", "reserved"];

/** Quiet "3.2k / 8k" meter: the next request's estimated size and, after an
 * answer, what the server actually reported. Details on click/focus. */
export function ContextIndicator({ conversationId }: { conversationId: string }) {
  const { t } = useTranslation("context");
  const chat = useChat((s) => s.convs[conversationId]);
  const draftChars = useUI((s) => s.draftChars);
  const [estimate, setEstimate] = useState<ContextEstimate | null>(null);
  const pins = chat?.messages.filter((m) => m.pinned).map((m) => m.id).join(",") ?? "";
  const refreshKey = `${chat?.streaming}|${chat?.activePath.join(",")}|${pins}`;

  useEffect(() => {
    if (!chat?.loaded || chat.streaming) return;
    let alive = true;
    api
      .contextEstimate(conversationId)
      .then((e) => alive && setEstimate(e))
      .catch(() => alive && setEstimate(null));
    return () => {
      alive = false;
    };
  }, [conversationId, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastAnswer = useMemo(() => pathMessages(chat).findLast((m) => m.role === "assistant" && m.status === "complete"), [chat]);
  if (!estimate || !estimate.window) return null;
  const draft = Math.ceil(draftChars / CHARS_PER_TOKEN);
  const used = (estimate.used ?? 0) + draft;
  const reserved = estimate.breakdown?.reserved ?? 0;
  const share = Math.min(1, (used + reserved) / estimate.window);
  const actual = lastAnswer?.metrics.prompt_tokens ? lastAnswer.metrics.prompt_tokens + (lastAnswer.metrics.completion_tokens ?? 0) : null;
  const value = t("indicator.value", { used: compactNumber(used), window: compactNumber(estimate.window) });

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={`${t("indicator.label")}: ${value}`}
          data-testid="context-indicator"
          className="flex h-8 shrink-0 items-center gap-2 rounded-lg px-2 text-[0.72rem] text-faint tabular-nums transition-colors hover:bg-hover hover:text-muted data-[state=open]:bg-hover"
        >
          <span className="relative h-1 w-10 overflow-hidden rounded-full bg-subtle" aria-hidden>
            <span
              className={cn("absolute inset-y-0 left-0 rounded-full", share > 0.9 || estimate.too_large ? "bg-warning" : "bg-accent/70")}
              style={{ width: `${Math.max(4, share * 100)}%` }}
            />
          </span>
          <span className="hidden sm:inline">{value}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className="animate-pop-in z-50 w-[min(20rem,calc(100vw-1.5rem))] rounded-xl border border-line bg-elevated p-3.5 text-[0.8125rem] shadow-pop focus:outline-none"
        >
          <p className="font-medium text-fg">{t("indicator.estimate")}</p>
          <p className="mt-0.5 text-muted">{value}</p>
          <dl className="mt-3 space-y-1">
            {PARTS.map((part) => {
              const n = estimate.breakdown?.[part] ?? 0;
              if (!n && part !== "history") return null;
              return (
                <div key={part} className="flex justify-between gap-3">
                  <dt className="text-muted">{t(`indicator.parts.${part}`)}</dt>
                  <dd className="text-fg tabular-nums">{formatNumber(n)}</dd>
                </div>
              );
            })}
            {draft > 0 && (
              <div className="flex justify-between gap-3">
                <dt className="text-muted">{t("indicator.parts.draft")}</dt>
                <dd className="text-fg tabular-nums">≈ {formatNumber(draft)}</dd>
              </div>
            )}
          </dl>
          <p className="mt-3 text-[0.75rem] leading-relaxed text-faint">
            {t("indicator.window", { window: formatNumber(estimate.window), source: t(`indicator.source.${estimate.window_source}`) })}{" "}
            {estimate.exact ? t("indicator.exact") : t("indicator.estimated")}
          </p>
          {estimate.strategy && <p className="mt-1 text-[0.75rem] text-faint">{t(`indicator.strategy.${estimate.strategy}`)}</p>}
          {actual != null && (
            <p className="mt-2 border-t border-line pt-2 text-[0.75rem] text-muted">
              {t("indicator.actual")}: {formatNumber(actual)}
            </p>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** Shown in the conversation where history stops being sent to the model. */
export function ContextMarker({
  dropped,
  summary,
  summaryFailed,
  retried,
  onChangeStrategy,
}: {
  dropped: number;
  summary: string | null | undefined;
  summaryFailed?: boolean;
  retried?: boolean;
  onChangeStrategy?: () => void;
}) {
  const { t } = useTranslation("context");
  const [open, setOpen] = useState(false);
  return (
    <div className="my-1" data-testid="context-marker" role="note">
      <div className="flex items-center gap-3 text-[0.75rem] text-faint">
        <span className="h-px flex-1 border-t border-dashed border-line-strong" aria-hidden />
        <span className="max-w-[85%] text-center leading-relaxed">
          {summary ? t("marker.summarized", { count: dropped }) : t("marker.dropped", { count: dropped })}
          {summaryFailed && <> {t("marker.summaryFailed")}</>}
          {retried && <> {t("marker.retried")}</>}{" "}
          {summary && (
            <button type="button" className="text-accent-text hover:underline" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
              {open ? t("marker.hideSummary") : t("marker.showSummary")}
            </button>
          )}
          {summary && onChangeStrategy && " · "}
          {onChangeStrategy && (
            <button type="button" className="text-accent-text hover:underline" onClick={onChangeStrategy}>
              {t("marker.change")}
            </button>
          )}
        </span>
        <span className="h-px flex-1 border-t border-dashed border-line-strong" aria-hidden />
      </div>
      {open && summary && (
        <section className="mx-auto mt-2 max-w-[90%] rounded-xl border border-line bg-subtle px-4 py-3" aria-label={t("marker.summaryTitle")}>
          <p className="text-[0.75rem] font-medium text-muted">{t("marker.summaryTitle")}</p>
          <p className="mt-0.5 text-[0.72rem] text-faint">{t("marker.summaryHelp")}</p>
          <p className="mt-2 text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-fg">{summary}</p>
        </section>
      )}
    </div>
  );
}
