import { BookOpen, Copy, MessageSquarePlus, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader, PageShell } from "@/components/Page";
import { Button, ConfirmDialog, Dialog, Field, Input, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, Spinner, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import type { Prompt } from "@/lib/types";
import { copyText } from "@/lib/utils";
import { toast, useUI } from "@/stores/ui";

export function PromptsPage() {
  const [items, setItems] = useState<Prompt[] | null>(null);
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Prompt | "new" | null>(null);
  const [deleting, setDeleting] = useState<Prompt | null>(null);
  const navigate = useNavigate();
  const insert = useUI((s) => s.insertIntoComposer);
  const setDraft = useUI((s) => s.setDraft);

  const load = () => api.prompts().then(setItems).catch((e) => toast(e.message, "error"));
  useEffect(() => {
    void load();
  }, []);

  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const list = (items ?? []).filter((p) => !q || [p.title, p.description, p.category, p.content].some((s) => s.toLowerCase().includes(q)));
    const map = new Map<string, Prompt[]>();
    for (const p of list) {
      const key = p.category || "Uncategorized";
      map.set(key, [...(map.get(key) ?? []), p]);
    }
    return [...map.entries()].sort(([a], [b]) => (a === "Uncategorized" ? 1 : b === "Uncategorized" ? -1 : a.localeCompare(b)));
  }, [items, filter]);

  const categories = useMemo(() => [...new Set((items ?? []).map((p) => p.category).filter(Boolean))], [items]);

  return (
    <PageShell>
      <PageHeader
        title="Prompts"
        description="Reusable instructions and templates. Insert them into a message, or use one as a conversation's system prompt."
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setEditing("new")}>
            New prompt
          </Button>
        }
      />
      {items && items.length > 0 && (
        <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter prompts" aria-label="Filter prompts" className="mb-6 max-w-sm" />
      )}
      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line-strong px-6 py-12 text-center">
          <BookOpen className="mx-auto mb-3 size-6 text-faint" />
          <p className="font-medium text-fg">Your prompt library is empty</p>
          <p className="mt-1 text-sm text-muted">Save instructions you use often — a code reviewer, a translator, a writing style.</p>
        </div>
      ) : (
        groups.map(([category, prompts]) => (
          <section key={category} className="mb-8">
            <h2 className="mb-2 text-[0.75rem] font-medium text-faint">{category}</h2>
            <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-elevated">
              {prompts.map((p) => (
                <li key={p.id} className="group flex items-start gap-3 px-4 py-3.5">
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setEditing(p)}>
                    <span className="block truncate text-sm font-medium text-fg">{p.title}</span>
                    <span className="mt-0.5 line-clamp-2 block text-[0.8125rem] leading-relaxed text-muted">{p.description || p.content}</span>
                  </button>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<MessageSquarePlus className="size-3.5" />}
                    onClick={() => {
                      insert(p.content);
                      navigate("/");
                    }}
                  >
                    Use
                  </Button>
                  <MenuRoot>
                    <MenuTrigger asChild>
                      <button type="button" aria-label={`Options for ${p.title}`} className="flex size-8 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-fg">
                        <MoreHorizontal className="size-4" />
                      </button>
                    </MenuTrigger>
                    <MenuContent align="end">
                      <MenuItem
                        icon={<MessageSquarePlus />}
                        onSelect={() => {
                          setDraft({ systemPrompt: p.content });
                          navigate("/");
                          toast(`New chat will use “${p.title}” as its system prompt`);
                        }}
                      >
                        New chat with this as system prompt
                      </MenuItem>
                      <MenuItem icon={<Copy />} onSelect={async () => (await copyText(p.content)) && toast("Copied")}>
                        Copy
                      </MenuItem>
                      <MenuItem icon={<Pencil />} onSelect={() => setEditing(p)}>
                        Edit
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem icon={<Trash2 />} destructive onSelect={() => setDeleting(p)}>
                        Delete
                      </MenuItem>
                    </MenuContent>
                  </MenuRoot>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
      <PromptDialog
        prompt={editing === "new" ? null : editing}
        open={editing !== null}
        categories={categories}
        onClose={() => setEditing(null)}
        onSaved={() => void load()}
      />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.title}”?`}
        onConfirm={async () => {
          if (deleting) await api.deletePrompt(deleting.id);
          await load();
        }}
      />
    </PageShell>
  );
}

function PromptDialog({ prompt, open, categories, onClose, onSaved }: { prompt: Prompt | null; open: boolean; categories: string[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ title: "", description: "", category: "", content: "" });
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) setForm({ title: prompt?.title ?? "", description: prompt?.description ?? "", category: prompt?.category ?? "", content: prompt?.content ?? "" });
  }, [open, prompt]);
  const save = async () => {
    setSaving(true);
    try {
      if (prompt) await api.updatePrompt(prompt.id, form);
      else await api.createPrompt(form);
      onSaved();
      onClose();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={prompt ? "Edit prompt" : "New prompt"}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!form.title.trim() || !form.content.trim()}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4 pb-3">
        <div className="grid gap-4 sm:grid-cols-[2fr_1fr]">
          <Field label="Title" htmlFor="pr-title">
            <Input id="pr-title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={200} autoFocus />
          </Field>
          <Field label="Category" htmlFor="pr-cat">
            <Input id="pr-cat" list="prompt-categories" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={80} placeholder="e.g. Code" />
            <datalist id="prompt-categories">
              {categories.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </Field>
        </div>
        <Field label="Description" htmlFor="pr-desc">
          <Input id="pr-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} maxLength={1000} placeholder="Optional" />
        </Field>
        <Field label="Prompt" htmlFor="pr-content">
          <Textarea id="pr-content" rows={10} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} className="font-mono text-[0.8125rem]" />
        </Field>
      </div>
    </Dialog>
  );
}
