import {
  BookOpen,
  Boxes,
  Library,
  LogOut,
  MoreHorizontal,
  PanelLeft,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  Trash2,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, useLocation, useNavigate, useParams } from "react-router-dom";
import { errorText } from "@/lib/errors";
import { useActiveModel } from "@/lib/hooks";
import { groupConversations } from "@/lib/time";
import type { Conversation } from "@/lib/types";
import { cn, modKey, modelLabel } from "@/lib/utils";
import { useAuth } from "@/stores/auth";
import { useChat } from "@/stores/chat";
import { useConversations } from "@/stores/conversations";
import { findProvider, useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast, useUI } from "@/stores/ui";
import { Logo, LogoMark } from "./Logo";
import { ConfirmDialog, IconButton, Kbd, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, StatusDot, Tooltip } from "./ui";
import { ThinkingIndicator } from "./ThinkingIndicator";

export function Sidebar() {
  const { t } = useTranslation(["sidebar", "common"]);
  const collapsed = useSettings((s) => s.settings?.sidebar_collapsed ?? false);
  const update = useSettings((s) => s.update);
  const mobileOpen = useUI((s) => s.mobileSidebarOpen);
  const setMobile = useUI((s) => s.setMobileSidebar);
  const location = useLocation();

  useEffect(() => setMobile(false), [location.pathname, setMobile]);

  const toggle = () => update({ sidebar_collapsed: !collapsed }).catch(() => undefined);

  return (
    <>
      <div
        className={cn("fixed inset-0 z-30 bg-black/25 transition-opacity md:hidden", mobileOpen ? "opacity-100" : "pointer-events-none opacity-0")}
        onClick={() => setMobile(false)}
        aria-hidden
      />
      <aside
        aria-label={t("label")}
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex flex-col border-r border-line bg-sidebar transition-[width,transform] duration-200 ease-out md:relative md:z-auto",
          "w-[var(--sidebar-width)]",
          mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
          collapsed && "md:w-[3.25rem]",
        )}
      >
        {collapsed ? <CollapsedRail onToggle={toggle} /> : <ExpandedSidebar onToggle={toggle} />}
      </aside>
    </>
  );
}

function CollapsedRail({ onToggle }: { onToggle: () => void }) {
  const { t } = useTranslation(["sidebar", "common"]);
  const navigate = useNavigate();
  const setSearchOpen = useUI((s) => s.setSearchOpen);
  return (
    <>
      <div className="hidden h-full flex-col items-center gap-1 py-3 md:flex">
        <IconButton label={t("expand")} side="right" onClick={onToggle} shortcut={`${modKey} B`}>
          <PanelLeft className="size-[18px]" />
        </IconButton>
        <div className="my-1" />
        <IconButton label={t("newChat")} side="right" shortcut={`${modKey} ⇧ O`} onClick={() => navigate("/")} className="text-accent hover:text-accent">
          <Plus className="size-[18px]" />
        </IconButton>
        <IconButton label={t("search")} side="right" shortcut={`${modKey} K`} onClick={() => setSearchOpen(true)}>
          <Search className="size-[18px]" />
        </IconButton>
        <IconButton label={t("models")} side="right" onClick={() => navigate("/models")}>
          <Boxes className="size-[18px]" />
        </IconButton>
        <IconButton label={t("knowledge")} side="right" onClick={() => navigate("/knowledge")}>
          <Library className="size-[18px]" />
        </IconButton>
        <IconButton label={t("prompts")} side="right" onClick={() => navigate("/prompts")}>
          <BookOpen className="size-[18px]" />
        </IconButton>
        <div className="flex-1" />
        <AccountMenu compact />
      </div>
      <div className="h-full md:hidden">
        <ExpandedSidebar onToggle={onToggle} />
      </div>
    </>
  );
}

function NavItem({ to, icon, children, onClick, shortcut }: { to?: string; icon: ReactNode; children: ReactNode; onClick?: () => void; shortcut?: string }) {
  const className = "group flex h-8 w-full min-w-0 items-center gap-2.5 rounded-lg px-2 text-[0.8125rem] text-muted transition-colors hover:bg-hover hover:text-fg";
  const content = (
    <>
      <span className="flex size-4 shrink-0 items-center justify-center [&>svg]:size-4">{icon}</span>
      <span className="min-w-0 flex-1 truncate text-left">{children}</span>
      {shortcut && (
        <span className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
          <Kbd>{shortcut}</Kbd>
        </span>
      )}
    </>
  );
  if (to)
    return (
      <NavLink to={to} className={({ isActive }) => cn(className, isActive && "bg-active text-fg")}>
        {content}
      </NavLink>
    );
  return (
    <button type="button" onClick={onClick} className={className}>
      {content}
    </button>
  );
}

function ExpandedSidebar({ onToggle }: { onToggle: () => void }) {
  const { t } = useTranslation(["sidebar", "common"]);
  const navigate = useNavigate();
  const setSearchOpen = useUI((s) => s.setSearchOpen);
  const { items, loaded } = useConversations();
  const groups = useMemo(() => groupConversations(items), [items]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between px-3">
        <button type="button" onClick={() => navigate("/")} className="rounded-md px-1" aria-label={t("home")}>
          <Logo />
        </button>
        <IconButton label={t("collapse")} onClick={onToggle} shortcut={`${modKey} B`} className="hidden md:inline-flex">
          <PanelLeft className="size-[18px]" />
        </IconButton>
      </div>

      <nav className="shrink-0 space-y-0.5 px-2" aria-label={t("primary")}>
        <button
          type="button"
          onClick={() => navigate("/")}
          className="group mb-1 flex h-9 w-full items-center gap-2.5 rounded-lg px-2 text-[0.8125rem] font-medium text-fg transition-colors hover:bg-hover"
        >
          <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent">
            <Plus className="size-3.5" strokeWidth={2.5} />
          </span>
          <span className="min-w-0 flex-1 truncate text-left">{t("newChat")}</span>
          <span className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100">
            <Kbd>{modKey}⇧O</Kbd>
          </span>
        </button>
        <NavItem icon={<Search />} onClick={() => setSearchOpen(true)} shortcut={`${modKey}K`}>
          {t("search")}
        </NavItem>
        <NavItem to="/models" icon={<Boxes />}>
          {t("models")}
        </NavItem>
        <NavItem to="/knowledge" icon={<Library />}>
          {t("knowledge")}
        </NavItem>
        <NavItem to="/prompts" icon={<BookOpen />}>
          {t("prompts")}
        </NavItem>
      </nav>

      <div className="mt-4 min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label={t("conversations")}>
        {loaded && items.length === 0 && <p className="px-2 py-2 text-[0.8125rem] text-faint">{t("empty")}</p>}
        {groups.map((g) => (
          <section key={g.key} className="mb-4">
            <h2 className="px-2 pb-1 text-[0.72rem] font-medium text-faint">{t(`groups.${g.key}`)}</h2>
            <ul className="space-y-px">
              {g.items.map((c) => (
                <ConversationItem key={c.id} conversation={c} />
              ))}
            </ul>
          </section>
        ))}
      </div>

      <SidebarFooter />
    </div>
  );
}

function ConversationItem({ conversation: c }: { conversation: Conversation }) {
  const { t } = useTranslation(["sidebar", "common"]);
  const { id: activeId } = useParams();
  const navigate = useNavigate();
  const patch = useConversations((s) => s.patch);
  const remove = useConversations((s) => s.remove);
  const forget = useChat((s) => s.forget);
  const streaming = useChat((s) => s.convs[c.id]?.streaming ?? false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState(c.title);
  const [confirm, setConfirm] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const active = activeId === c.id;

  useEffect(() => {
    if (renaming) {
      setTitle(c.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [renaming, c.title]);

  const commit = async () => {
    setRenaming(false);
    const next = title.trim();
    if (next && next !== c.title) {
      try {
        await patch(c.id, { title: next });
      } catch (err) {
        toast(errorText(err), "error");
      }
    }
  };

  return (
    <li className="group relative">
      {renaming ? (
        <input
          ref={inputRef}
          value={title}
          aria-label={t("titleLabel")}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setRenaming(false);
          }}
          className="h-8 w-full rounded-lg border border-accent bg-elevated px-2 text-[0.8125rem] text-fg focus:outline-none"
          maxLength={200}
        />
      ) : (
        <NavLink
          to={`/c/${c.id}`}
          title={c.title}
          className={cn(
            "flex h-8 items-center gap-2 rounded-lg pr-8 pl-2 text-[0.8125rem] transition-colors",
            active ? "bg-active text-fg" : "text-muted hover:bg-hover hover:text-fg",
          )}
          onDoubleClick={() => setRenaming(true)}
        >
          {/* Keyed by the title: a new title (e.g. generated live) fades in. */}
          <span key={c.title} className="animate-title-in truncate">
            {c.title}
          </span>
          {(streaming || c.generating) && (
            <span className="ml-auto inline-flex shrink-0">
              <ThinkingIndicator size="sm" />
              <span className="sr-only">{t("generating")}</span>
            </span>
          )}
        </NavLink>
      )}
      {!renaming && (
        <MenuRoot>
          <MenuTrigger asChild>
            <button
              type="button"
              aria-label={t("optionsFor", { title: c.title })}
              className={cn(
                "absolute top-1 right-1 flex size-6 items-center justify-center rounded-md text-muted transition-opacity hover:bg-active hover:text-fg data-[state=open]:opacity-100",
                active ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
              )}
            >
              <MoreHorizontal className="size-4" />
            </button>
          </MenuTrigger>
          <MenuContent align="start">
            <MenuItem icon={c.pinned ? <PinOff /> : <Pin />} onSelect={() => patch(c.id, { pinned: !c.pinned }).catch((e) => toast(errorText(e), "error"))}>
              {c.pinned ? t("unpin") : t("pin")}
            </MenuItem>
            <MenuItem icon={<Pencil />} onSelect={() => setRenaming(true)}>
              {t("rename")}
            </MenuItem>
            <MenuSeparator />
            <MenuItem icon={<Trash2 />} destructive onSelect={() => setConfirm(true)}>
              {t("common:delete")}
            </MenuItem>
          </MenuContent>
        </MenuRoot>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("deleteTitle")}
        description={t("deleteBody", { title: c.title })}
        onConfirm={async () => {
          try {
            await remove(c.id);
            forget(c.id);
            if (active) navigate("/");
          } catch (err) {
            toast(errorText(err), "error");
          }
        }}
      />
    </li>
  );
}

function AccountMenu({ compact }: { compact?: boolean }) {
  const { t } = useTranslation(["sidebar", "common"]);
  const navigate = useNavigate();
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  if (!user) return null;
  const initials = user.username.slice(0, 2).toUpperCase();
  return (
    <MenuRoot>
      <Tooltip label={t("account")} side={compact ? "right" : "top"}>
        <MenuTrigger asChild>
          <button
            type="button"
            aria-label={t("accountMenu", { username: user.username })}
            className="flex size-8 shrink-0 items-center justify-center rounded-lg text-[0.72rem] font-semibold text-muted transition-colors hover:bg-hover hover:text-fg data-[state=open]:bg-hover"
          >
            <span className="flex size-6 items-center justify-center rounded-full bg-subtle text-fg">{initials}</span>
          </button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align={compact ? "start" : "end"} side={compact ? "right" : "top"}>
        <div className="px-2.5 pt-1.5 pb-2">
          <p className="truncate text-[0.8125rem] font-medium text-fg">{user.username}</p>
          <p className="truncate text-[0.75rem] text-muted">{user.email}</p>
        </div>
        <MenuSeparator />
        <MenuItem icon={<UserRound />} onSelect={() => navigate("/settings/account")}>
          {t("accountSettings")}
        </MenuItem>
        <MenuItem icon={<Settings />} onSelect={() => navigate("/settings")}>
          {t("settings")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<LogOut />} onSelect={() => logout()}>
          {t("signOut")}
        </MenuItem>
      </MenuContent>
    </MenuRoot>
  );
}

function SidebarFooter() {
  const { t } = useTranslation(["sidebar", "common"]);
  const navigate = useNavigate();
  const { id } = useParams();
  const conv = useConversations((s) => s.items.find((c) => c.id === id));
  const entries = useModels((s) => s.entries);
  const active = useActiveModel(conv);
  const model = active?.model;
  const provider = findProvider(entries, active?.providerId);

  return (
    <div className="shrink-0 border-t border-line p-2">
      <div className="flex items-center gap-1">
        <Tooltip label={t("manageModels")} side="top">
          <button
            type="button"
            onClick={() => navigate("/models")}
            className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-hover"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-subtle text-muted">
              <LogoMark className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[0.8125rem] font-medium text-fg" title={model ?? undefined}>
                {model ? modelLabel(model) : t("noModel")}
              </span>
              <span className="block truncate text-[0.72rem] text-muted">
                {provider ? <StatusDot status={provider.status} withLabel /> : t("setUpConnection")}
                {provider && <span className="text-faint"> · {provider.name}</span>}
              </span>
            </span>
          </button>
        </Tooltip>
        <AccountMenu />
      </div>
    </div>
  );
}
