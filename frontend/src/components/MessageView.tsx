import {
  AlertTriangle,
  Check,
  ChevronRight,
  Copy,
  FileText,
  Pencil,
  Pin,
  PinOff,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorText, providerErrorText, serverDetail } from "@/lib/errors";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/time";
import type { Attachment, Message, Source } from "@/lib/types";
import { cn, copyText, formatBytes, formatNumber, modelLabel } from "@/lib/utils";
import { LogoMark } from "./Logo";
import { Markdown } from "./Markdown";
import { BranchNav } from "./BranchNav";
import { Button, ConfirmDialog, Dialog, IconButton, Spinner } from "./ui";
import { citationTargets, LinkStatus, visibleRefs, WebSourceList, WebSteps, type WebSources } from "./WebParts";
import { ThinkingIndicator } from "./ThinkingIndicator";

/** Sibling navigation for a message that has other versions. */
export interface BranchInfo {
  index: number;
  total: number;
  onSelect: (index: number) => void;
}

function PinButton({ pinned, onToggle }: { pinned: boolean; onToggle: () => void }) {
  const { t } = useTranslation("context");
  return (
    <IconButton label={pinned ? t("pin.unpin") : t("pin.pin")} size="sm" onClick={onToggle} active={pinned}>
      {pinned ? <PinOff className="size-3.5" /> : <Pin className="size-3.5" />}
    </IconButton>
  );
}

function PinnedBadge() {
  const { t } = useTranslation("context");
  return (
    <span className="flex items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[0.68rem] text-accent-text" title={t("pin.pinnedHelp")}>
      <Pin className="size-3" aria-hidden /> {t("pin.pinned")}
    </span>
  );
}

function CopyButton({ text, label }: { text: string; label?: string }) {
  const { t } = useTranslation(["chat", "common"]);
  const [copied, setCopied] = useState(false);
  return (
    <IconButton
      label={copied ? t("message.copied") : label ?? t("message.copy")}
      size="sm"
      onClick={async () => {
        if (await copyText(text)) {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </IconButton>
  );
}

/* --------------------------------------------------------------- user turn */
export function UserMessage({
  message,
  disabled,
  onEdit,
  onDelete,
  webSources = {},
  branch,
  deleteCount = 1,
  onTogglePin,
}: {
  message: Message;
  disabled: boolean;
  onEdit: (content: string) => Promise<void>;
  onDelete: () => void;
  webSources?: WebSources;
  branch?: BranchInfo;
  deleteCount?: number;
  onTogglePin?: () => void;
}) {
  const links = useMemo(
    () => Object.values(webSources).filter((s) => s.message_id === message.id && s.origin === "pasted").sort((a, b) => a.position - b.position),
    [webSources, message.id],
  );
  const { t } = useTranslation(["chat", "common"]);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);
  const [confirm, setConfirm] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (editing && ref.current) {
      const ta = ref.current;
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 400)}px`;
    }
  }, [editing]);

  const save = async () => {
    if (!draft.trim() && !message.attachments.length) return;
    setEditing(false);
    await onEdit(draft);
  };

  return (
    <div className="group flex flex-col items-end">
      {message.attachments.length > 0 && (
        <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-1.5">
          {message.attachments.map((a) => (
            <AttachmentPreview key={a.id} attachment={a} />
          ))}
        </div>
      )}
      {editing ? (
        <div className="w-full rounded-2xl border border-line-strong bg-elevated p-3">
          <textarea
            ref={ref}
            value={draft}
            aria-label={t("message.editLabel")}
            onChange={(e) => {
              setDraft(e.target.value);
              e.target.style.height = "auto";
              e.target.style.height = `${Math.min(e.target.scrollHeight, 400)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setEditing(false);
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void save();
            }}
            className="block w-full resize-none bg-transparent text-[0.95rem] leading-relaxed text-fg focus:outline-none"
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <span className="mr-auto text-[0.72rem] text-faint">{t("message.editWarning")}</span>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              {t("common:cancel")}
            </Button>
            <Button size="sm" variant="primary" onClick={save} disabled={!draft.trim() && !message.attachments.length}>
              {t("message.saveSubmit")}
            </Button>
          </div>
        </div>
      ) : (
        message.content && (
          <div className="max-w-[85%] rounded-2xl bg-subtle px-4 py-2.5 text-[0.95rem] leading-relaxed break-words whitespace-pre-wrap text-fg">
            {message.content}
          </div>
        )
      )}
      {!editing && <LinkStatus sources={links} conversationId={message.conversation_id} />}
      {!editing && (
        <div className="mt-1 flex h-7 items-center gap-1">
          <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 max-md:opacity-100">
            <time className="mr-1.5 text-[0.72rem] text-faint" dateTime={message.created_at}>
              {formatTime(message.created_at)}
            </time>
            <CopyButton text={message.content} />
            {!message.id.startsWith("temp-") && (
              <>
                {onTogglePin && <PinButton pinned={!!message.pinned} onToggle={onTogglePin} />}
                <IconButton label={t("message.edit")} size="sm" disabled={disabled} onClick={() => { setDraft(message.content); setEditing(true); }}>
                  <Pencil className="size-3.5" />
                </IconButton>
                <IconButton label={t("message.deleteMessage")} size="sm" disabled={disabled} onClick={() => setConfirm(true)}>
                  <Trash2 className="size-3.5" />
                </IconButton>
              </>
            )}
          </div>
          {message.pinned && <PinnedBadge />}
          {branch && <BranchNav kind="user" index={branch.index} total={branch.total} onSelect={branch.onSelect} />}
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("message.deleteMessageTitle")}
        description={t("message.deleteCount", { count: deleteCount })}
        onConfirm={onDelete}
      />
    </div>
  );
}

function AttachmentPreview({ attachment }: { attachment: Attachment }) {
  const { t } = useTranslation(["chat", "common"]);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isImage = attachment.kind === "image";

  useEffect(() => {
    if (!open || isImage || text !== null) return;
    api
      .file(attachment.id)
      .then((f) => setText(f.text ?? ""))
      .catch((e) => setError(errorText(e)));
  }, [open, isImage, attachment.id, text]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex h-11 max-w-[14rem] items-center gap-2 rounded-xl border border-line bg-elevated pr-3 pl-1.5 text-left transition-colors hover:bg-hover"
      >
        <span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-subtle text-muted">
          {isImage ? (
            <img src={`/api/files/${attachment.id}/content`} alt="" className="size-full object-cover" onError={(e) => (e.currentTarget.style.display = "none")} />
          ) : (
            <FileText className="size-4" />
          )}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[0.8125rem] text-fg">{attachment.filename}</span>
          <span className="block text-[0.7rem] text-faint">
            {formatBytes(attachment.size)}
            {attachment.truncated && ` · ${t("message.truncated")}`}
          </span>
        </span>
      </button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={attachment.filename}
        description={isImage ? t("message.imageAttachment") : t("message.extractedText")}
        size="lg"
      >
        {isImage ? (
          <img src={`/api/files/${attachment.id}/content`} alt={attachment.filename} className="mx-auto max-h-[60vh] rounded-lg" />
        ) : error ? (
          <p className="text-sm text-danger">{error}</p>
        ) : text === null ? (
          <Spinner />
        ) : (
          <pre className="mb-4 max-h-[60vh] overflow-auto rounded-lg bg-code p-4 font-mono text-[0.78rem] leading-relaxed whitespace-pre-wrap">{text}</pre>
        )}
      </Dialog>
    </>
  );
}

/* ---------------------------------------------------------- assistant turn */
export function AssistantMessage({
  message,
  isLast,
  busy,
  showReasoning,
  onRegenerate,
  onDelete,
  webSources = {},
  expandSteps = false,
  branch,
  deleteCount = 1,
  onTogglePin,
  contextMarker,
}: {
  message: Message;
  isLast: boolean;
  busy: boolean;
  showReasoning: boolean;
  /** ``webSearch`` overrides whether the new answer searches the web. */
  onRegenerate: (webSearch?: boolean) => void;
  onDelete: () => void;
  webSources?: WebSources;
  expandSteps?: boolean;
  branch?: BranchInfo;
  deleteCount?: number;
  onTogglePin?: () => void;
  /** Rendered above the answer (e.g. the trimmed-history marker). */
  contextMarker?: React.ReactNode;
}) {
  const { t } = useTranslation(["chat", "common", "web"]);
  const [confirm, setConfirm] = useState(false);
  const streaming = message.status === "streaming";
  const webRunning = !!message.web?.steps?.some((s) => s.status === "running");
  const waiting = streaming && !message.content && !message.reasoning && !webRunning;
  const citations = useMemo(() => citationTargets(message, webSources), [message, webSources]);
  const webError = message.status === "error" && !!message.error_code?.startsWith("web.");
  const m = message.metrics;

  return (
    <div className="group">
      {contextMarker}
      <div className="mb-2 flex items-center gap-2 text-[0.8125rem] text-muted">
        <LogoMark className="size-[18px] text-accent" />
        <span className="truncate font-medium text-fg" title={message.model ?? undefined}>{modelLabel(message.model) || t("message.unknownModel")}</span>
      </div>

      <WebSteps message={message} sources={webSources} defaultOpen={expandSteps} />

      {showReasoning && message.reasoning && <Reasoning text={message.reasoning} live={streaming && !message.content} />}

      {waiting ? (
        // Exactly one line of answer text tall, so the first streamed line takes its place without a jump.
        <div className="flex h-[1.72rem] items-center gap-2" role="status" aria-label={t("message.waiting")}>
          <ThinkingIndicator />
          <span className="text-[0.8125rem] text-faint">{t("message.thinking")}</span>
        </div>
      ) : (
        message.content && <Markdown content={message.content} streaming={streaming} citations={citations} />
      )}

      {message.sources.length > 0 && <Sources sources={message.sources} />}
      <WebSourceList refs={visibleRefs(message)} sources={webSources} conversationId={message.conversation_id} />

      {message.status === "error" && (
        <div role="alert" className="mt-3 flex items-start gap-3 rounded-xl border border-danger/25 bg-danger-soft px-3.5 py-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-fg">{t("message.failed")}</p>
            <p className="mt-0.5 text-[0.8125rem] leading-relaxed break-words text-muted">
              {providerErrorText(message.error_code, message.error_params, message.error)}
            </p>
            {serverDetail(message.error_params) && (
              <p className="mt-1 font-mono text-[0.75rem] break-words text-faint">{serverDetail(message.error_params)}</p>
            )}
          </div>
          {isLast && webError ? (
            <div className="flex shrink-0 flex-col gap-1.5 sm:flex-row">
              <Button size="sm" variant="outline" icon={<RotateCcw className="size-3.5" />} onClick={() => onRegenerate(true)} disabled={busy}>
                {t("web:actions.retrySearch")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onRegenerate(false)} disabled={busy}>
                {t("web:actions.withoutSearch")}
              </Button>
            </div>
          ) : isLast ? (
            <Button size="sm" variant="outline" icon={<RotateCcw className="size-3.5" />} onClick={() => onRegenerate()} disabled={busy}>
              {t("message.retry")}
            </Button>
          ) : null}
        </div>
      )}
      {(message.status === "cancelled" || message.status === "interrupted") && (
        <p className="mt-2 flex items-center gap-2 text-[0.8125rem] text-faint">
          <span className="inline-block size-1.5 rounded-full bg-faint" aria-hidden />
          {message.status === "cancelled" ? t("message.stopped") : message.error_code === "interrupted" || !message.error_code ? t("message.interrupted") : providerErrorText(message.error_code, message.error_params, message.error)}
          {isLast && (
            <button type="button" className="text-accent-text hover:underline disabled:opacity-50" onClick={() => onRegenerate()} disabled={busy}>
              {t("message.regenerate")}
            </button>
          )}
        </p>
      )}

      {!streaming && (
        <div className="mt-2 flex h-7 items-center gap-1">
        {branch && <BranchNav kind="assistant" index={branch.index} total={branch.total} onSelect={branch.onSelect} />}
        {message.pinned && <PinnedBadge />}
        <div className="flex min-w-0 items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100 max-md:opacity-100 data-[last=true]:opacity-100" data-last={isLast}>
          {message.content && <CopyButton text={message.content} label={t("message.copyResponse")} />}
          {onTogglePin && message.content && <PinButton pinned={!!message.pinned} onToggle={onTogglePin} />}
          <IconButton label={t("message.regenerate")} size="sm" onClick={() => onRegenerate()} disabled={busy}>
            <RotateCcw className="size-3.5" />
          </IconButton>
          <IconButton label={t("message.deleteResponse")} size="sm" onClick={() => setConfirm(true)} disabled={busy}>
            <Trash2 className="size-3.5" />
          </IconButton>
          <span className="ml-2 flex min-w-0 items-center gap-2 truncate font-mono text-[0.7rem] text-faint">
            <time dateTime={message.created_at}>{formatTime(message.created_at)}</time>
            {m.tokens_per_second ? <span>· {t("message.tokensPerSecond", { value: formatNumber(Number(m.tokens_per_second.toFixed(1))) })}</span> : null}
            {m.completion_tokens ? <span>· {t("message.tokens", { count: m.completion_tokens })}</span> : null}
            {m.finish_reason === "length" && <span className="text-warning">· {t("message.hitMaxTokens")}</span>}
          </span>
        </div>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("message.deleteResponseTitle")}
        description={t("message.deleteCount", { count: deleteCount })}
        onConfirm={onDelete}
      />
    </div>
  );
}

function Reasoning({ text, live }: { text: string; live: boolean }) {
  const { t } = useTranslation(["chat", "common"]);
  const [open, setOpen] = useState(false);
  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded-md py-0.5 text-[0.8125rem] text-muted transition-colors hover:text-fg"
      >
        {live ? <ThinkingIndicator className="mr-0.5 text-[0.875rem]" /> : <ChevronRight className={cn("size-3.5 transition-transform", open && "rotate-90")} />}
        {live ? t("message.thinking") : t("message.thoughtProcess")}
      </button>
      {open && (
        <div className="mt-1.5 ml-1.5 border-l-2 border-line pl-4 text-[0.875rem] leading-relaxed whitespace-pre-wrap text-muted">{text}</div>
      )}
    </div>
  );
}

function Sources({ sources }: { sources: Source[] }) {
  const { t } = useTranslation(["chat", "common"]);
  const [active, setActive] = useState<Source | null>(null);
  return (
    <div className="mt-3">
      <p className="mb-1.5 text-[0.72rem] font-medium text-faint">{t("message.sources")}</p>
      <div className="flex flex-wrap gap-1.5">
        {sources.map((s) => (
          <button
            key={s.n}
            type="button"
            onClick={() => setActive(s)}
            className="flex h-7 max-w-[16rem] items-center gap-1.5 rounded-lg border border-line px-2 text-[0.75rem] text-muted transition-colors hover:bg-hover hover:text-fg"
          >
            <span className="font-mono text-accent-text">{s.n}</span>
            <span className="truncate">{s.filename}</span>
          </button>
        ))}
      </div>
      <Dialog
        open={!!active}
        onOpenChange={(o) => !o && setActive(null)}
        title={active ? `[${active.n}] ${active.filename}` : ""}
        description={active ? t("message.excerpt", { n: active.chunk_index + 1, method: t(`method.${active.method as "keyword" | "semantic" | "hybrid"}`) }) : undefined}
      >
        <p className="mb-4 text-sm leading-relaxed whitespace-pre-wrap text-fg">{active?.text}</p>
      </Dialog>
    </div>
  );
}

