import * as DialogPrimitive from "@radix-ui/react-dialog";
import { MessageSquare, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/time";
import type { Conversation } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useConversations } from "@/stores/conversations";
import { useUI } from "@/stores/ui";
import { Kbd, Spinner } from "./ui";

export function SearchDialog() {
  const { t } = useTranslation("sidebar");
  const open = useUI((s) => s.searchOpen);
  const setOpen = useUI((s) => s.setSearchOpen);
  const recent = useConversations((s) => s.items);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Conversation[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setResults(null);
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      return;
    }
    setLoading(true);
    const t = setTimeout(() => {
      api
        .searchConversations(q)
        .then((r) => {
          setResults(r);
          setActive(0);
        })
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 160);
    return () => clearTimeout(t);
  }, [query]);

  const items = results ?? recent.slice(0, 12);

  const go = (c: Conversation) => {
    setOpen(false);
    navigate(`/c/${c.id}`);
  };

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="animate-fade-in fixed inset-0 z-40 bg-black/25 dark:bg-black/50" />
        <DialogPrimitive.Content
          className="animate-pop-in fixed top-[12vh] left-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-line bg-elevated shadow-pop focus:outline-none"
          aria-describedby={undefined}
        >
          <DialogPrimitive.Title className="sr-only">{t("searchDialog.title")}</DialogPrimitive.Title>
          <div className="flex items-center gap-3 border-b border-line px-4">
            <Search className="size-4 shrink-0 text-faint" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, items.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter" && items[active]) {
                  e.preventDefault();
                  go(items[active]);
                }
              }}
              placeholder={t("searchDialog.placeholder")}
              aria-label={t("searchDialog.title")}
              role="combobox"
              aria-expanded="true"
              aria-controls="search-results"
              aria-activedescendant={items[active] ? `search-item-${items[active].id}` : undefined}
              className="h-12 flex-1 bg-transparent text-[0.95rem] text-fg placeholder:text-faint focus:outline-none"
            />
            {loading && <Spinner />}
          </div>
          <ul id="search-results" ref={listRef} role="listbox" className="max-h-[55vh] overflow-y-auto p-1.5">
            {!results && items.length > 0 && <li className="px-3 pt-1.5 pb-1 text-[0.72rem] font-medium text-faint">{t("searchDialog.recent")}</li>}
            {items.length === 0 && (
              <li className="px-3 py-8 text-center text-sm text-muted">{query.trim() ? t("searchDialog.noResults") : t("empty")}</li>
            )}
            {items.map((c, i) => (
              <li key={c.id} id={`search-item-${c.id}`} role="option" aria-selected={i === active} data-index={i}>
                <button
                  type="button"
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(c)}
                  className={cn("flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left", i === active && "bg-hover")}
                >
                  <MessageSquare className="mt-0.5 size-4 shrink-0 text-faint" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-fg">{c.title}</span>
                    {c.snippet && <span className="mt-0.5 line-clamp-2 block text-[0.78rem] text-muted">{c.snippet}</span>}
                  </span>
                  <span className="shrink-0 text-[0.72rem] text-faint">{formatTime(c.updated_at)}</span>
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-[0.72rem] text-faint">
            <span>
              <Kbd>↑</Kbd> <Kbd>↓</Kbd> {t("searchDialog.navigate")}
            </span>
            <span>
              <Kbd>↵</Kbd> {t("searchDialog.open")}
            </span>
            <span>
              <Kbd>esc</Kbd> {t("searchDialog.close")}
            </span>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
