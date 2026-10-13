import * as Popover from "@radix-ui/react-popover";
import { ArrowUp, BookOpen, FileText, Image as ImageIcon, Library, Loader2, Paperclip, SlidersHorizontal, Square, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { errorText } from "@/lib/errors";
import { api } from "@/lib/api";
import type { Attachment, ModelRef, Prompt } from "@/lib/types";
import { cn, formatBytes, modKey } from "@/lib/utils";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";
import { ModelPicker } from "./ModelPicker";
import { IconButton, Tooltip } from "./ui";

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

export interface ComposerProps {
  onSend: (content: string, attachments: Attachment[]) => Promise<void>;
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
}: ComposerProps) {
  const { t } = useTranslation("chat");
  const [text, setText] = useState("");
  const [files, setFiles] = useState<PendingFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const enterToSend = useSettings((s) => s.settings?.enter_to_send ?? true);
  const insert = useUI((s) => s.composerInsert);
  const clearInsert = useUI((s) => s.insertIntoComposer);

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

  const submit = async () => {
    if (!canSend) return;
    const content = text;
    const snapshot = files;
    setSubmitting(true);
    setText("");
    setFiles([]);
    try {
      await onSend(content, ready);
      snapshot.forEach((f) => f.previewUrl && URL.revokeObjectURL(f.previewUrl));
    } catch {
      // Restore what the user typed so nothing is lost.
      setText((t) => (t ? t : content));
      setFiles((f) => (f.length ? f : snapshot));
    } finally {
      setSubmitting(false);
      requestAnimationFrame(() => textareaRef.current?.focus());
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
          className="block max-h-[40vh] min-h-[3.25rem] w-full resize-none bg-transparent px-4 pt-3.5 pb-1 text-[0.95rem] leading-relaxed text-fg placeholder:text-faint focus:outline-none focus-visible:outline-none"
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
                className="ml-0.5 flex h-7 items-center gap-1 rounded-md bg-accent-soft px-2 text-[0.75rem] font-medium text-accent-text"
              >
                <Library className="size-3.5" /> {knowledgeCount}
              </button>
            </Tooltip>
          )}
          <div className="flex-1" />
          <ModelPicker value={model} onChange={onModelChange} compact />
          {streaming ? (
            <IconButton
              label={t("composer.stop")}
              shortcut="Esc"
              onClick={onStop}
              className="ml-1 size-8 rounded-lg bg-fg text-bg hover:bg-fg hover:text-bg hover:opacity-85"
            >
              <Square className="size-3 fill-current" />
            </IconButton>
          ) : (
            <IconButton
              label={t("composer.send")}
              shortcut={enterToSend ? "Enter" : `${modKey} ↵`}
              onClick={submit}
              disabled={!canSend}
              className={cn(
                "ml-1 size-8 rounded-lg transition-colors",
                canSend ? "bg-accent text-on-accent hover:bg-accent-hover hover:text-on-accent" : "bg-subtle text-faint",
              )}
            >
              {submitting ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-[18px]" strokeWidth={2.25} />}
            </IconButton>
          )}
        </div>
      </div>
      <p id="composer-hint" className={cn("min-h-5 px-2 pt-1.5 text-center text-[0.72rem]", hint ? "text-muted" : "text-faint")} aria-live="polite">
        {hint ?? (enterToSend ? t("composer.hintEnter") : t("composer.hintMod", { key: modKey }))}
      </p>
    </div>
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
          <Loader2 className="size-4 animate-spin" />
        ) : file.previewUrl ? (
          <img src={file.previewUrl} alt="" className="size-full object-cover" />
        ) : isImage ? (
          <ImageIcon className="size-4" />
        ) : (
          <FileText className="size-4" />
        )}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[0.8125rem] font-medium text-fg">{file.name}</span>
        <span className={cn("block truncate text-[0.72rem]", file.status === "error" || blocked ? "text-danger" : "text-faint")}>
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
                <span className="block truncate text-[0.75rem] text-faint">{p.description || p.content}</span>
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
              className="flex h-8 w-full items-center rounded-lg px-3 text-[0.8125rem] text-muted hover:bg-hover hover:text-fg"
            >
              {t("composer.managePrompts")}
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
