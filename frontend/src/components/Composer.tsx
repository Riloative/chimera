import * as Popover from "@radix-ui/react-popover";
import { ArrowUp, BookOpen, FileText, Image as ImageIcon, Library, Link2, Paperclip, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { errorText } from "@/lib/errors";
import { api } from "@/lib/api";
import type { Attachment, ModelRef, Prompt, SearchMode, ThinkingCaps, ThinkingLevel } from "@/lib/types";
import { dedupeKey, findUrls, shortUrl } from "@/lib/urls";
import { cn, formatBytes, modKey } from "@/lib/utils";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";
import { useWeb } from "@/stores/web";
import { ModelPicker } from "./ModelPicker";
import { SearchControl } from "./SearchControl";
import { ThinkingControl } from "./ThinkingControl";
import { Button, Dialog, IconButton, Tooltip } from "./ui";
import { ThinkingIndicator } from "./ThinkingIndicator";

interface PendingFile {
  key: string;
  name: string;
  size: number;
  status: "uploading" | "ready" | "error";
  error?: string;
  attachment?: Attachment;
  previewUrl?: string;
}

const ACCEPT =
  ".txt,.md,.markdown,.rst,.log,.csv,.tsv,.json,.jsonl,.yaml,.yml,.toml,.ini,.cfg,.conf,.xml,.html,.htm,.css,.scss,.sql,.tex,.py,.js,.mjs,.ts,.tsx,.jsx,.vue,.svelte,.c,.h,.cc,.cpp,.hpp,.cs,.java,.kt,.scala,.go,.rs,.rb,.php,.swift,.lua,.r,.jl,.sh,.bash,.zsh,.ps1,.dart,.ex,.exs,.hs,.zig,.proto,.graphql,.diff,.patch,.pdf,.png,.jpg,.jpeg,.webp,.gif,Dockerfile,Makefile";

/** What the composer asks the server to do on the web for this message. */
export interface WebChoice {
  urls: string[] | null;
  /** A one-off choice for this message; null = the conversation's search mode. */
  web_search: boolean | null;
}

export interface ComposerProps {
  onSend: (content: string, attachments: Attachment[], web: WebChoice) => Promise<void>;
  onStop?: () => void;
  streaming?: boolean;
  model: ModelRef | null;
  onModelChange: (ref: ModelRef) => void;
  visionSupported: boolean;
  onOpenSettings?: () => void;
  settingsActive?: boolean;
  knowledgeCount?: number;
  autoFocus?: boolean;
  placeholder?: string;
  conversationKey?: string;
  /** Web search in this conversation (or the start page's choice). */
  searchMode?: SearchMode;
  onSearchModeChange?: (mode: SearchMode) => void;
  /** Thinking in this conversation and what the current model can do. */
  thinking?: ThinkingLevel;
  thinkingCaps?: ThinkingCaps;
  onThinkingChange?: (level: ThinkingLevel) => void;
}

export function Composer({
  onSend,
  onStop,
  streaming,
  model,
  onModelChange,
  visionSupported,
  onOpenSettings,
  settingsActive,
  knowledgeCount = 0,
  autoFocus,
  placeholder,
  conversationKey,
  searchMode = "off",
  onSearchModeChange,
  thinking = "default",
  thinkingCaps,
  onThinkingChange,
}: ComposerProps) {
  const { t } = useTranslation(["chat", "web"]);
  const key = conversationKey ?? "new";
  // Each conversation keeps its own unsent text (the composer stays mounted when you switch).
  const [text, setText] = useState(() => useUI.getState().composerDrafts[key] ?? "");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const enterToSend = useSettings((s) => s.settings?.enter_to_send ?? true);
  const insert = useUI((s) => s.composerInsert);
  const clearInsert = useUI((s) => s.insertIntoComposer);
  const webStatus = useWeb((s) => s.status);
  const webPrefs = useSettings((s) => s.settings?.web);
  const override = useWeb((s) => s.override[key]);
  const setOverride = useWeb((s) => s.setOverride);
  const searchAvailable = !!webStatus?.search_available;
  const [privacyOpen, setPrivacyOpen] = useState(false);
  // What to do once the user has read what web search sends (first use only).
  const pendingConsent = useRef<(() => void) | null>(null);
  // Links the user chose to (not) read, by normalized URL.
  const [linkChoice, setLinkChoice] = useState<Record<string, boolean>>({});
  const detected = useMemo(() => findUrls(text, 20), [text]);
  const webEnabled = !!webStatus?.enabled;
  const maxLinks = webStatus?.max_urls_per_message ?? 5;
  const autoRead = webPrefs?.auto_read_links ?? true;
  const shownLinks = webEnabled ? detected.urls.slice(0, maxLinks) : [];
  const linkSelected = (url: string) => linkChoice[dedupeKey(url)] ?? autoRead;

  const setDraftChars = useUI((s) => s.setDraftChars);
  const setComposerDraft = useUI((s) => s.setComposerDraft);
  const shownKey = useRef(key);
  const textNow = useRef(text);
  textNow.current = text;
  const submittingNow = useRef(false);
  // Switching conversations: keep this draft, show the other conversation's.
  // A message being sent from the start page moves with its new conversation.
  useLayoutEffect(() => {
    const previous = shownKey.current;
    if (previous === key) return;
    shownKey.current = key;
    if (previous === "new" && submittingNow.current) {
      setComposerDraft("new", "");
      return;
    }
    setComposerDraft(previous, textNow.current);
    setText(useUI.getState().composerDrafts[key] ?? "");
    setFiles([]);
    setLinkChoice({});
  }, [key, setComposerDraft]);
  useEffect(() => {
    setComposerDraft(shownKey.current, text);
    const id = setTimeout(() => setDraftChars(shownKey.current, text.length), 250);
    return () => clearTimeout(id);
  }, [text, setDraftChars, setComposerDraft]);

  // Auto-grow the textarea up to a comfortable maximum.
  useLayoutEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, window.innerHeight * 0.4)}px`;
  }, [text]);

  useEffect(() => {
    if (autoFocus && window.matchMedia("(pointer: fine)").matches) textareaRef.current?.focus();
  }, [autoFocus, conversationKey]);

  useEffect(() => {
    if (insert == null) return;
    setText((t) => (t.trim() ? `${t}\n\n${insert}` : insert));
    clearInsert(null);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [insert, clearInsert]);

  const addFiles = useCallback((list: FileList | File[]) => {
    for (const file of Array.from(list).slice(0, 10)) {
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2)}`;
      const previewUrl = file.type.startsWith("image/") ? URL.createObjectURL(file) : undefined;
      setFiles((f) => [...f, { key, name: file.name, size: file.size, status: "uploading", previewUrl }]);
      api
        .uploadFile(file)
        .then((attachment) => setFiles((f) => f.map((p) => (p.key === key ? { ...p, status: "ready", attachment } : p))))
        .catch((err) => setFiles((f) => f.map((p) => (p.key === key ? { ...p, status: "error", error: errorText(err) } : p))));
    }
  }, []);

  const removeFile = (key: string) => {
    setFiles((f) => {
      const target = f.find((p) => p.key === key);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      if (target?.attachment) void api.deleteFile(target.attachment.id).catch(() => undefined);
      return f.filter((p) => p.key !== key);
    });
  };

  const ready = files.filter((f) => f.status === "ready" && f.attachment).map((f) => f.attachment!);
  const uploading = files.some((f) => f.status === "uploading");
  const hasBlockedImage = ready.some((a) => a.kind === "image") && !visionSupported;
  const hasErrors = files.some((f) => f.status === "error");
  const canSend = !streaming && !submitting && !uploading && !hasBlockedImage && !hasErrors && !!model && (text.trim().length > 0 || ready.length > 0);

  const withConsent = (turnsSearchOn: boolean, action: () => void) => {
    if (turnsSearchOn && !webPrefs?.privacy_acknowledged) {
      pendingConsent.current = action;
      setPrivacyOpen(true);
      return;
    }
    action();
  };

  const submit = async () => {
    if (!canSend) return;
    const content = text;
    const snapshot = files;
    const web: WebChoice = {
      urls: webEnabled ? shownLinks.filter(linkSelected) : null,
      web_search: searchAvailable && override !== undefined ? override : null,
    };
    setSubmitting(true);
    submittingNow.current = true;
    setText("");
    setFiles([]);
    // A one-off choice applies to this message only. Clear it now (before a new
    // conversation takes over), restore it if sending fails.
    if (override !== undefined) setOverride(key, null);
    try {
      await onSend(content, ready, web);
      snapshot.forEach((f) => f.previewUrl && URL.revokeObjectURL(f.previewUrl));
      setLinkChoice({});
    } catch {
      // Restore what the user typed so nothing is lost.
      setText((t) => (t ? t : content));
      setFiles((f) => (f.length ? f : snapshot));
      if (web.web_search !== null) setOverride(key, web.web_search);
    } finally {
      setSubmitting(false);
      submittingNow.current = false;
      // Back to the message box, unless the user has moved on (e.g. opened a menu meanwhile).
      requestAnimationFrame(() => {
        const active = document.activeElement;
        if (!active || active === document.body || active === textareaRef.current) textareaRef.current?.focus();
      });
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Escape" && streaming) {
      e.preventDefault();
      onStop?.();
      return;
    }
    if (e.key !== "Enter") return;
    const mod = e.metaKey || e.ctrlKey;
    if ((enterToSend && !e.shiftKey && !mod) || (!enterToSend && mod)) {
      e.preventDefault();
      void submit();
    }
  };

  const onPaste = (e: ClipboardEvent) => {
    const pasted = Array.from(e.clipboardData.files);
    if (pasted.length) {
      e.preventDefault();
      addFiles(pasted);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  let hint: string | null = null;
  if (!model) hint = t("composer.selectModel");
  else if (hasBlockedImage) hint = t("composer.noVision");
  else if (hasErrors) hint = t("composer.removeFailed");
  else if (searchAvailable && override !== undefined) hint = override ? t("web:composer.oneOffOn") : t("web:composer.oneOffOff");
  else if (webStatus && !webEnabled && detected.urls.length) hint = t("web:composer.linksOff");
  else if (webEnabled && detected.total > maxLinks) hint = t("web:composer.overLimit", { count: maxLinks });

  return (
    <div className="w-full">
      <div
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes("Files")) {
            e.preventDefault();
            setDragging(true);
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "relative rounded-2xl border bg-elevated transition-[border-color,box-shadow] duration-150",
          "border-line-strong shadow-[0_1px_2px_rgb(0_0_0/0.03),0_4px_16px_rgb(0_0_0/0.04)] focus-within:border-[color-mix(in_oklab,var(--accent)_30%,var(--border-strong))]",
          dragging && "border-accent ring-4 ring-accent-soft",
        )}
      >
        {shownLinks.length > 0 && (
          <ul className="flex flex-wrap gap-1.5 px-3 pt-3" aria-label={t("web:composer.linksLabel")}>
            {shownLinks.map((url) => (
              <LinkChip
                key={dedupeKey(url)}
                url={url}
                selected={linkSelected(url)}
                onToggle={() => setLinkChoice((c) => ({ ...c, [dedupeKey(url)]: !linkSelected(url) }))}
              />
            ))}
          </ul>
        )}
        {files.length > 0 && (
          <ul className="flex flex-wrap gap-2 px-3 pt-3" aria-label={t("composer.attachments")}>
            {files.map((f) => (
              <AttachmentChip key={f.key} file={f} visionSupported={visionSupported} onRemove={() => removeFile(f.key)} />
            ))}
          </ul>
        )}
        <label htmlFor="composer-input" className="sr-only">
          {t("composer.label")}
        </label>
        <textarea
          id="composer-input"
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          rows={1}
          placeholder={placeholder ?? t("composer.placeholder")}
          className="block max-h-[40vh] min-h-[3.25rem] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-lead leading-relaxed text-fg placeholder:text-faint focus:outline-none focus-visible:outline-none"
          aria-describedby="composer-hint"
        />
        <div className="flex items-center gap-1 px-2 pb-2">
          <input
            ref={fileRef}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              if (e.target.files) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <IconButton label={t("composer.attach")} onClick={() => fileRef.current?.click()}>
            <Paperclip className="size-[18px]" />
          </IconButton>
          <PromptInsert onInsert={(p) => setText((t) => (t.trim() ? `${t}\n\n${p.content}` : p.content))} />
          {webEnabled && (
            <SearchControl
              mode={searchMode}
              override={searchAvailable ? override : undefined}
              available={searchAvailable}
              autoAvailable={!!webStatus?.auto_available}
              isAdmin={!!webStatus?.is_admin}
              onModeChange={(m) => withConsent(m !== "off", () => onSearchModeChange?.(m))}
              onOverrideChange={(v) => withConsent(v === true, () => setOverride(key, v))}
            />
          )}
          {onOpenSettings && (
            <IconButton label={t("settings.title")} onClick={onOpenSettings} active={settingsActive}>
              <SlidersHorizontal className="size-[18px]" />
            </IconButton>
          )}
          {knowledgeCount > 0 && (
            <Tooltip label={t("composer.knowledgeActive", { count: knowledgeCount })}>
              <button
                type="button"
                onClick={onOpenSettings}
                className="ml-0.5 flex h-7 items-center gap-1 rounded-md bg-accent-soft px-2 text-caption font-medium text-accent-text"
              >
                <Library className="size-3.5" /> {knowledgeCount}
              </button>
            </Tooltip>
          )}
          <div className="flex-1" />
          {onThinkingChange && <ThinkingControl caps={thinkingCaps} level={thinking} onChange={onThinkingChange} />}
          <ModelPicker value={model} onChange={onModelChange} compact />
          {streaming ? (
            // Same size, shape and colour as Send, so nothing moves when they swap; only the glyph changes.
            <IconButton
              label={t("composer.stop")}
              shortcut="Esc"
              onClick={onStop}
              data-testid="stop-button"
              className="ml-1 size-8 rounded-lg bg-accent text-on-accent hover:bg-accent-hover hover:text-on-accent"
            >
              <span aria-hidden className="size-2.5 rounded-[2.5px] bg-current" />
            </IconButton>
          ) : (
            <IconButton
              label={t("composer.send")}
              data-testid="send-button"
              shortcut={enterToSend ? "Enter" : `${modKey} ↵`}
              onClick={submit}
              disabled={!canSend}
              className={cn(
                "ml-1 size-8 rounded-lg transition-colors",
                canSend ? "bg-accent text-on-accent hover:bg-accent-hover hover:text-on-accent" : "bg-subtle text-faint",
              )}
            >
              {submitting ? <ThinkingIndicator className="text-base" /> : <ArrowUp className="size-[18px]" strokeWidth={2.25} />}
            </IconButton>
          )}
        </div>
      </div>
      <p id="composer-hint" className={cn("min-h-5 px-2 pt-1.5 text-center text-caption", hint ? "text-muted" : "text-faint")} aria-live="polite">
        {hint ?? (enterToSend ? t("composer.hintEnter") : t("composer.hintMod", { key: modKey }))}
      </p>
      <PrivacyNotice
        open={privacyOpen}
        onOpenChange={setPrivacyOpen}
        engine={webStatus?.engine ?? null}
        onAccept={() => {
          setPrivacyOpen(false);
          pendingConsent.current?.();
          pendingConsent.current = null;
          void useSettings
            .getState()
            .update({ web: { ...(webPrefs ?? ({} as NonNullable<typeof webPrefs>)), privacy_acknowledged: true } })
            .catch(() => undefined);
        }}
      />
    </div>
  );
}

function LinkChip({ url, selected, onToggle }: { url: string; selected: boolean; onToggle: () => void }) {
  const { t } = useTranslation("web");
  const short = shortUrl(url, 40);
  return (
    <li>
      <Tooltip label={selected ? t("composer.willRead", { url }) : t("composer.skipped", { url })}>
        <button
          type="button"
          onClick={onToggle}
          aria-pressed={selected}
          aria-label={selected ? t("composer.skipLink", { url }) : t("composer.readLink", { url })}
          className={cn(
            "flex h-7 max-w-[min(18rem,calc(100vw-5rem))] items-center gap-1.5 rounded-lg border pr-1.5 pl-2 text-caption transition-colors",
            selected ? "border-line bg-bg text-fg hover:bg-hover" : "border-dashed border-line text-faint hover:text-muted",
          )}
        >
          <Link2 className={cn("size-3.5 shrink-0", selected ? "text-accent" : "")} aria-hidden />
          <span className={cn("truncate", !selected && "line-through")}>{short}</span>
          <span className="ml-0.5 shrink-0 rounded px-1 text-caption text-muted">{selected ? <X className="size-3" aria-hidden /> : t("composer.read")}</span>
        </button>
      </Tooltip>
    </li>
  );
}

function PrivacyNotice({
  open,
  onOpenChange,
  engine,
  onAccept,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  engine: "searxng" | "brave" | "tavily" | null;
  onAccept: () => void;
}) {
  const { t } = useTranslation("web");
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("privacy.title")}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("privacy.cancel")}
          </Button>
          <Button variant="primary" onClick={onAccept}>
            {t("privacy.accept")}
          </Button>
        </>
      }
    >
      <div className="space-y-2.5 pb-3 text-[0.875rem] leading-relaxed text-muted">
        <p className="text-fg">{t("privacy.intro")}</p>
        {engine && <p>{t(`privacy.engine.${engine}`)}</p>}
        <p>{t("privacy.sites")}</p>
        <p>{t("privacy.links")}</p>
      </div>
    </Dialog>
  );
}

function AttachmentChip({ file, visionSupported, onRemove }: { file: PendingFile; visionSupported: boolean; onRemove: () => void }) {
  const { t } = useTranslation("chat");
  const isImage = file.attachment?.kind === "image" || !!file.previewUrl;
  const blocked = isImage && file.status === "ready" && !visionSupported;
  return (
    <li
      className={cn(
        "group relative flex h-12 max-w-[15rem] items-center gap-2.5 rounded-xl border bg-bg pr-8 pl-1.5 text-left",
        file.status === "error" || blocked ? "border-danger/40" : "border-line",
      )}
      title={file.error ?? file.name}
    >
      <span className="flex size-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-subtle text-muted">
        {file.status === "uploading" ? (
          <ThinkingIndicator className="text-base" />
        ) : file.previewUrl ? (
          <img src={file.previewUrl} alt="" className="size-full object-cover" />
        ) : isImage ? (
          <ImageIcon className="size-4" />
        ) : (
          <FileText className="size-4" />
        )}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-ui font-medium text-fg">{file.name}</span>
        <span className={cn("block truncate text-caption", file.status === "error" || blocked ? "text-danger" : "text-faint")}>
          {file.status === "error"
            ? file.error
            : blocked
              ? t("composer.cantReadImages")
              : file.status === "uploading"
                ? t("composer.reading")
                : file.attachment?.truncated
                  ? t("composer.truncated", { size: formatBytes(file.size) })
                  : formatBytes(file.size)}
        </span>
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={t("composer.remove", { name: file.name })}
        className="absolute top-1/2 right-1.5 flex size-6 -translate-y-1/2 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-fg"
      >
        <X className="size-3.5" />
      </button>
    </li>
  );
}

function PromptInsert({ onInsert }: { onInsert: (p: Prompt) => void }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  const [prompts, setPrompts] = useState<Prompt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    if (!open) return;
    api
      .prompts()
      .then((p) => {
        setPrompts(p);
        setError(null);
      })
      .catch((e) => setError(errorText(e)));
  }, [open]);

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Tooltip label={t("composer.insertPrompt")}>
        <Popover.Trigger asChild>
          <button
            type="button"
            aria-label={t("composer.insertPrompt")}
            className="inline-flex size-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-fg data-[state=open]:bg-hover"
          >
            <BookOpen className="size-[18px]" />
          </button>
        </Popover.Trigger>
      </Tooltip>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={6}
          className="animate-pop-in z-50 w-[min(22rem,calc(100vw-1.5rem))] rounded-xl border border-line bg-elevated p-1 shadow-pop focus:outline-none"
        >
          <div className="max-h-72 overflow-y-auto">
            {error && <p className="px-3 py-3 text-sm text-danger">{error}</p>}
            {prompts && prompts.length === 0 && <p className="px-3 py-4 text-center text-sm text-muted">{t("composer.noPrompts")}</p>}
            {prompts?.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => {
                  onInsert(p);
                  setOpen(false);
                }}
                className="block w-full rounded-lg px-3 py-2 text-left hover:bg-hover"
              >
                <span className="block truncate text-sm text-fg">{p.title}</span>
                <span className="block truncate text-caption text-faint">{p.description || p.content}</span>
              </button>
            ))}
          </div>
          <div className="mt-1 border-t border-line pt-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate("/prompts");
              }}
              className="flex h-8 w-full items-center rounded-lg px-3 text-ui text-muted hover:bg-hover hover:text-fg"
            >
              {t("composer.managePrompts")}
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
