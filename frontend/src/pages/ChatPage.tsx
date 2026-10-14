import { AlertTriangle, ArrowDown, Download, Menu, MoreHorizontal, Pencil, Pin, PinOff, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { errorText, providerErrorText } from "@/lib/errors";
import { Composer, type WebChoice } from "@/components/Composer";
import { ConversationSettings, type ConversationSettingsValue } from "@/components/ConversationSettings";
import { LogoMark } from "@/components/Logo";
import { AssistantMessage, UserMessage } from "@/components/MessageView";
import type { WebSources } from "@/components/WebParts";
import { Button, ConfirmDialog, Dialog, IconButton, Input, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useActiveModel } from "@/lib/hooks";
import { greetingKey } from "@/lib/time";
import type { Attachment, Conversation, Message, ModelRef } from "@/lib/types";
import { cn, modelLabel } from "@/lib/utils";
import { useChat } from "@/stores/chat";
import { useConversations } from "@/stores/conversations";
import { findProvider, modelSupportsVision, useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast, useUI } from "@/stores/ui";
import { browserTimeZone, useWeb } from "@/stores/web";

const EXAMPLES = ["explain", "review", "write", "plan"] as const;

export function ChatPage() {
  const { t } = useTranslation(["chat", "common", "sidebar"]);
  const { id } = useParams();
  const navigate = useNavigate();
  const conv = useConversations((s) => s.items.find((c) => c.id === id));
  const convsLoaded = useConversations((s) => s.loaded);
  const chat = useChat((s) => (id ? s.convs[id] : undefined));
  const { load, send, regenerate, edit, stop, deleteMessage, clearSendError } = useChat.getState();
  const entries = useModels((s) => s.entries);
  const modelsLoaded = useModels((s) => s.loaded);
  const settings = useSettings((s) => s.settings);
  const setSelectedModel = useUI((s) => s.setSelectedModel);
  const draft = useUI((s) => s.draft);
  const setDraft = useUI((s) => s.setDraft);
  const resetDraft = useUI((s) => s.resetDraft);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const model = useActiveModel(conv);
  const provider = findProvider(entries, model?.providerId);
  const vision = modelSupportsVision(entries, model);

  useEffect(() => {
    if (id && !useChat.getState().convs[id]?.loaded) void load(id);
  }, [id, load]);

  useEffect(() => {
    document.title = conv?.title && id ? `${conv.title} · Chimera` : "Chimera";
  }, [conv?.title, id]);

  const target = useCallback(
    (ref: ModelRef | null) => (ref ? { provider_id: ref.providerId, model: ref.model } : {}),
    [],
  );

  const onModelChange = async (ref: ModelRef) => {
    setSelectedModel(ref);
    if (id && conv) {
      try {
        await useConversations.getState().patch(id, { provider_id: ref.providerId, model: ref.model });
      } catch (err) {
        toast(errorText(err), "error");
      }
    }
  };

  const onSend = async (content: string, attachments: Attachment[], web: WebChoice) => {
    if (!model) return;
    const body = { content, attachments: attachments.map((a) => a.id), ...target(model), ...web, timezone: browserTimeZone() };
    let convId = id;
    if (!convId) {
      const created = await api
        .createConversation({
          provider_id: model.providerId,
          model: model.model,
          system_prompt: draft.systemPrompt,
          params: draft.params,
          collection_ids: draft.collectionIds,
        })
        .catch((err) => {
          toast(errorText(err), "error");
          throw err;
        });
      useConversations.getState().upsert(created);
      convId = created.id;
      resetDraft();
      useWeb.getState().moveSearch("new", convId);
      const p = send(convId, body, attachments);
      navigate(`/c/${convId}`, { replace: true });
      // Rejections propagate so the composer restores what was typed.
      await p;
      return;
    }
    await send(convId, body, attachments);
  };

  const settingsValue: ConversationSettingsValue = conv
    ? { systemPrompt: conv.system_prompt, params: conv.params, collectionIds: conv.collection_ids }
    : draft;
  const saveSettings = async (v: ConversationSettingsValue) => {
    if (id && conv) {
      try {
        await useConversations.getState().patch(id, { system_prompt: v.systemPrompt, params: v.params, collection_ids: v.collectionIds });
        toast(t("settings.saved"));
      } catch (err) {
        toast(errorText(err), "error");
        throw err;
      }
    } else setDraft(v);
  };
  const customized = conv
    ? conv.system_prompt != null || Object.keys(conv.params ?? {}).length > 0
    : draft.systemPrompt != null || Object.keys(draft.params).length > 0;
  const knowledgeCount = (conv ? conv.collection_ids : draft.collectionIds).length;

  const composer = (
    <Composer
      onSend={onSend}
      onStop={() => id && stop(id)}
      streaming={!!chat?.streaming}
      model={model}
      onModelChange={onModelChange}
      visionSupported={vision}
      onOpenSettings={() => setSettingsOpen(true)}
      settingsActive={customized}
      knowledgeCount={knowledgeCount}
      autoFocus
      conversationKey={id ?? "new"}
    />
  );

  const settingsDialog = (
    <ConversationSettings
      open={settingsOpen}
      onOpenChange={setSettingsOpen}
      value={settingsValue}
      onSave={saveSettings}
      supportedParams={provider?.supported_params}
    />
  );

  if (!id) {
    const noConnections = modelsLoaded && entries.length === 0;
    const noModels = modelsLoaded && entries.length > 0 && !model;
    return (
      <div className="flex h-full flex-col">
        <TopBar />
        <main className="flex min-h-0 flex-1 flex-col items-center overflow-y-auto px-4 pb-6">
          <div className="flex w-full max-w-[var(--chat-width)] flex-1 flex-col justify-center pb-[8vh]">
            <div className="animate-fade-in mb-8 flex items-center justify-center gap-3">
              <LogoMark className="size-8 text-accent" />
              <h1 className="text-[1.75rem] font-medium tracking-[-0.02em] text-fg">{t(`greeting.${greetingKey()}`)}</h1>
            </div>
            {(noConnections || noModels) && <SetupCard noConnections={noConnections} />}
            {composer}
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => useUI.getState().insertIntoComposer(t(`examples.${ex}.text`))}
                  className="h-8 rounded-lg border border-line px-3 text-[0.8125rem] text-muted transition-colors hover:border-line-strong hover:bg-hover hover:text-fg"
                >
                  {t(`examples.${ex}.label`)}
                </button>
              ))}
            </div>
          </div>
        </main>
        {settingsDialog}
      </div>
    );
  }

  if (chat?.loadError || (convsLoaded && !conv && chat?.loaded)) {
    return (
      <div className="flex h-full flex-col">
        <TopBar />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
          <p className="text-fg">{t("loadFailed")}</p>
          <p className="text-sm text-muted">{chat?.loadError ?? t("maybeDeleted")}</p>
          <Button variant="outline" onClick={() => navigate("/")}>
            {t("startNew")}
          </Button>
        </div>
      </div>
    );
  }

  const messages = chat?.messages ?? [];
  const busy = !!chat?.streaming;

  return (
    <div className="flex h-full flex-col">
      <TopBar conversation={conv} model={model} onOpenSettings={() => setSettingsOpen(true)} messages={messages} />
      <MessageList
        key={id}
        messages={messages}
        loading={!chat?.loaded}
        busy={busy}
        showReasoning={settings?.show_reasoning ?? true}
        expandSteps={settings?.web?.expand_steps ?? false}
        webSources={chat?.webSources ?? {}}
        onRegenerate={(mid, webSearch) =>
          regenerate(id, mid, { ...target(model), timezone: browserTimeZone(), ...(webSearch === undefined ? {} : { web_search: webSearch }) }).catch((e) =>
            toast(errorText(e), "error"),
          )
        }
        onEdit={(mid, content) => edit(id, mid, content, { ...target(model), timezone: browserTimeZone() }).catch((e) => toast(errorText(e), "error"))}
        onDelete={(mid) => deleteMessage(id, mid).catch((e) => toast(errorText(e), "error"))}
      >
        <div className="mx-auto w-full max-w-[var(--chat-width)] px-4 pb-3 md:px-6">
          {provider && provider.status === "error" && (
            <ProviderBanner providerId={provider.id} name={provider.name} error={providerErrorText(provider.last_error_code, provider.last_error_params, provider.last_error)} />
          )}
          {chat?.sendError && (
            <div role="alert" className="mb-3 flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger-soft px-3.5 py-2.5 text-[0.8125rem]">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
              <span className="flex-1 text-fg">{chat.sendError}</span>
              <button type="button" aria-label={t("common:dismiss")} onClick={() => clearSendError(id)} className="text-muted hover:text-fg">
                <X className="size-4" />
              </button>
            </div>
          )}
          {composer}
        </div>
      </MessageList>
      {settingsDialog}
    </div>
  );
}

function SetupCard({ noConnections }: { noConnections: boolean }) {
  const { t } = useTranslation(["chat", "common", "sidebar"]);
  return (
    <div className="mb-4 rounded-2xl border border-line bg-elevated px-5 py-4">
      <p className="text-sm font-medium text-fg">{noConnections ? t("setup.connectTitle") : t("setup.noModelsTitle")}</p>
      <p className="mt-1 text-[0.8125rem] leading-relaxed text-muted">
        {noConnections ? (
          <>
            {t("setup.connectBefore")} <code className="rounded bg-subtle px-1 font-mono text-[0.78rem]">llama-server -m model.gguf --port 8080</code>
            {t("setup.connectAfter")}
          </>
        ) : (
          t("setup.noModelsBody")
        )}
      </p>
      <Link to="/models" className="mt-3 inline-flex h-8 items-center rounded-lg bg-fg px-3 text-[0.8125rem] font-medium text-bg hover:opacity-90">
        {noConnections ? t("setup.add") : t("setup.open")}
      </Link>
    </div>
  );
}

function ProviderBanner({ providerId, name, error }: { providerId: string; name: string; error: string | null }) {
  const { t } = useTranslation(["chat", "common", "sidebar"]);
  const [testing, setTesting] = useState(false);
  const loadModels = useModels((s) => s.load);
  return (
    <div role="status" className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border border-line bg-elevated px-3.5 py-2.5 text-[0.8125rem]">
      <span className="flex items-center gap-2 text-fg">
        <span className="size-1.5 rounded-full bg-danger" aria-hidden />
        <span className="font-medium">{t("banner.unreachable", { name })}</span>
      </span>
      {error && <span className="min-w-0 flex-1 truncate text-muted" title={error}>{error}</span>}
      <span className="ml-auto flex gap-1">
        <Button
          size="sm"
          variant="ghost"
          loading={testing}
          onClick={async () => {
            setTesting(true);
            try {
              const r = await api.testProvider(providerId);
              toast(r.ok ? t("banner.reachable", { name }) : providerErrorText(r.error_code, r.error_params, r.error) || t("banner.still"), r.ok ? "success" : "error");
              await loadModels();
            } catch (err) {
              toast(errorText(err), "error");
            } finally {
              setTesting(false);
            }
          }}
        >
          {t("banner.retry")}
        </Button>
        <Link to="/models" className="inline-flex h-8 items-center rounded-lg px-2.5 text-[0.8125rem] text-muted hover:bg-hover hover:text-fg">
          {t("banner.settings")}
        </Link>
      </span>
    </div>
  );
}

function TopBar({
  conversation,
  model,
  onOpenSettings,
  messages,
}: {
  conversation?: Conversation;
  model?: ModelRef | null;
  onOpenSettings?: () => void;
  messages?: Message[];
}) {
  const { t } = useTranslation(["chat", "common", "sidebar"]);
  const setMobile = useUI((s) => s.setMobileSidebar);
  const navigate = useNavigate();
  const patch = useConversations((s) => s.patch);
  const remove = useConversations((s) => s.remove);
  const forget = useChat((s) => s.forget);
  const [renameOpen, setRenameOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [confirm, setConfirm] = useState(false);

  const exportMarkdown = () => {
    if (!conversation || !messages) return;
    const lines = [`# ${conversation.title}`, ""];
    for (const m of messages) {
      lines.push(m.role === "user" ? `## ${t("export.you")}` : `## ${modelLabel(m.model)}`, "", m.content, "");
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${conversation.title.replace(/[^\w\- ]+/g, "").slice(0, 60) || "conversation"}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 px-3 md:px-4">
      <IconButton label={t("sidebar:open")} className="md:hidden" onClick={() => setMobile(true)}>
        <Menu className="size-[18px]" />
      </IconButton>
      {conversation && (
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <MenuRoot>
            <MenuTrigger asChild>
              <button
                type="button"
                className="flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-1 text-sm transition-colors hover:bg-hover data-[state=open]:bg-hover"
              >
                <span className="truncate font-medium text-fg">{conversation.title}</span>
                <MoreHorizontal className="size-4 shrink-0 text-faint" />
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuItem
                icon={<Pencil />}
                onSelect={() => {
                  setTitle(conversation.title);
                  setRenameOpen(true);
                }}
              >
                {t("sidebar:rename")}
              </MenuItem>
              <MenuItem icon={conversation.pinned ? <PinOff /> : <Pin />} onSelect={() => patch(conversation.id, { pinned: !conversation.pinned })}>
                {conversation.pinned ? t("sidebar:unpin") : t("sidebar:pin")}
              </MenuItem>
              <MenuItem icon={<SlidersHorizontal />} onSelect={() => onOpenSettings?.()}>
                {t("settings.title")}
              </MenuItem>
              <MenuItem icon={<Download />} onSelect={exportMarkdown}>
                {t("export.markdown")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem icon={<Trash2 />} destructive onSelect={() => setConfirm(true)}>
                {t("common:delete")}
              </MenuItem>
            </MenuContent>
          </MenuRoot>
          {model && <span className="hidden truncate text-[0.8125rem] text-faint sm:inline">· {modelLabel(model.model)}</span>}
        </div>
      )}
      <Dialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        title={t("renameTitle")}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenameOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button
              variant="primary"
              disabled={!title.trim()}
              onClick={async () => {
                if (conversation) await patch(conversation.id, { title: title.trim() }).catch((e) => toast(errorText(e), "error"));
                setRenameOpen(false);
              }}
            >
              {t("common:save")}
            </Button>
          </>
        }
      >
        <form
          className="pb-2"
          onSubmit={async (e) => {
            e.preventDefault();
            if (conversation && title.trim()) await patch(conversation.id, { title: title.trim() }).catch((err) => toast(errorText(err), "error"));
            setRenameOpen(false);
          }}
        >
          <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus aria-label={t("sidebar:titleLabel")} />
        </form>
      </Dialog>
      {conversation && (
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title={t("sidebar:deleteTitle")}
          description={t("sidebar:deleteBody", { title: conversation.title })}
          onConfirm={async () => {
            await remove(conversation.id);
            forget(conversation.id);
            navigate("/");
          }}
        />
      )}
    </header>
  );
}

function MessageList({
  messages,
  loading,
  busy,
  showReasoning,
  expandSteps,
  webSources,
  onRegenerate,
  onEdit,
  onDelete,
  children,
}: {
  messages: Message[];
  loading: boolean;
  busy: boolean;
  showReasoning: boolean;
  expandSteps: boolean;
  webSources: WebSources;
  onRegenerate: (messageId?: string, webSearch?: boolean) => void;
  onEdit: (messageId: string, content: string) => Promise<void> | void;
  onDelete: (messageId: string) => void;
  children: React.ReactNode;
}) {
  const { t } = useTranslation(["chat", "common", "sidebar"]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const lastUserId = messages.findLast((m) => m.role === "user")?.id;

  const scrollToBottom = (smooth = false) => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };

  // Follow the stream only while the user is at the bottom.
  useLayoutEffect(() => {
    if (stick.current) scrollToBottom();
  }, [messages]);

  // Keep pinned to the bottom when content or the viewport changes size.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stick.current) scrollToBottom();
    });
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, []);

  // A new user turn always brings the view down.
  useEffect(() => {
    stick.current = true;
    scrollToBottom();
  }, [lastUserId]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    stick.current = distance < 80;
    setShowJump(distance > 240);
  };

  const lastAssistant = messages.findLast((m) => m.role === "assistant")?.id;

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto [overflow-anchor:none]">
        <div className="mx-auto w-full max-w-[var(--chat-width)] px-4 pt-4 pb-10 md:px-6">
          {loading && messages.length === 0 ? (
            <div className="flex justify-center py-16">
              <Spinner />
            </div>
          ) : (
            <ol className="space-y-8" aria-label={t("messages")}>
              {messages.map((m) => (
                <li key={m.id} className={cn("animate-fade-in", m.role === "assistant" && "pr-1")}>
                  {m.role === "user" ? (
                    <UserMessage
                      message={m}
                      disabled={busy}
                      webSources={webSources}
                      onEdit={async (c) => void (await onEdit(m.id, c))}
                      onDelete={() => onDelete(m.id)}
                    />
                  ) : m.role === "assistant" ? (
                    <AssistantMessage
                      message={m}
                      isLast={m.id === lastAssistant}
                      busy={busy}
                      showReasoning={showReasoning}
                      webSources={webSources}
                      expandSteps={expandSteps}
                      onRegenerate={(webSearch) => onRegenerate(m.id, webSearch)}
                      onDelete={() => onDelete(m.id)}
                    />
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
      <div className="relative shrink-0 bg-bg">
        {showJump && (
          <button
            type="button"
            onClick={() => {
              stick.current = true;
              scrollToBottom(true);
            }}
            className="animate-fade-in absolute -top-11 left-1/2 z-10 flex h-8 -translate-x-1/2 items-center gap-1.5 rounded-full border border-line bg-elevated px-3 text-[0.8125rem] text-muted shadow-pop hover:text-fg"
            aria-label={t("jumpLatest")}
          >
            <ArrowDown className="size-3.5" /> {t("latest")}
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
