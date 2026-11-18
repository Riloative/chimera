import { AlertTriangle, ArrowLeft, FileText, Library, MoreHorizontal, Pencil, Plus, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";
import { errorText } from "@/lib/errors";
import { PageHeader, PageShell, Section } from "@/components/Page";
import { Button, ConfirmDialog, Dialog, Field, IconButton, Input, MenuContent, MenuItem, MenuRoot, MenuTrigger, Spinner, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/time";
import type { Collection, KnowledgeDocument, SearchHit } from "@/lib/types";
import { cn, formatBytes, formatNumber, modelLabel } from "@/lib/utils";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";
import { ThinkingIndicator } from "@/components/ThinkingIndicator";

export function KnowledgePage() {
  const { id } = useParams();
  return id ? <CollectionDetail id={id} /> : <CollectionList />;
}

function CollectionDialog({ open, onOpenChange, collection, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; collection?: Collection; onSaved: (c: Collection) => void }) {
  const { t } = useTranslation(["knowledge", "chat", "common"]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (open) {
      setName(collection?.name ?? "");
      setDescription(collection?.description ?? "");
    }
  }, [open, collection]);
  const save = async () => {
    setSaving(true);
    try {
      const c = collection ? await api.updateCollection(collection.id, { name, description }) : await api.createCollection({ name, description });
      onSaved(c);
      onOpenChange(false);
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={collection ? t("editCollection") : t("newCollection")}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!name.trim()}>
            {collection ? t("common:save") : t("common:create")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4 pb-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) void save();
        }}
      >
        <Field label={t("name")} htmlFor="col-name">
          <Input id="col-name" value={name} onChange={(e) => setName(e.target.value)} autoFocus maxLength={120} placeholder={t("namePlaceholder")} />
        </Field>
        <Field label={t("descriptionLabel")} htmlFor="col-desc">
          <Textarea id="col-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} />
        </Field>
      </form>
    </Dialog>
  );
}

function CollectionList() {
  const { t } = useTranslation(["knowledge", "chat", "common"]);
  const [items, setItems] = useState<Collection[] | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();
  const knowledge = useSettings((s) => s.settings?.knowledge);

  useEffect(() => {
    api.collections().then(setItems).catch((e) => toast(errorText(e), "error"));
  }, []);

  return (
    <PageShell>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            {t("newCollection")}
          </Button>
        }
      />
      <p className="mb-6 rounded-xl bg-subtle px-4 py-3 text-[0.8125rem] leading-relaxed text-muted">
        {knowledge?.embedding_model ? (
          <Trans
            t={t}
            i18nKey="modeHybrid"
            values={{ model: modelLabel(knowledge.embedding_model) }}
            components={{ strong: <span className="font-medium text-fg" />, code: <span className="font-mono text-fg" /> }}
          />
        ) : (
          <Trans
            t={t}
            i18nKey="modeKeyword"
            components={{ strong: <span className="font-medium text-fg" />, settingsLink: <Link to="/settings/documents" className="text-accent-text hover:underline" /> }}
          />
        )}
      </p>
      {items === null ? (
        <Spinner />
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-line-strong px-6 py-12 text-center">
          <Library className="mx-auto mb-3 size-6 text-faint" />
          <p className="font-medium text-fg">{t("empty.title")}</p>
          <p className="mt-1 text-sm text-muted">{t("empty.body")}</p>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {items.map((c) => (
            <li key={c.id}>
              <Link to={`/knowledge/${c.id}`} className="block rounded-2xl border border-line bg-elevated px-4 py-3.5 transition-colors hover:border-line-strong">
                <span className="block truncate text-sm font-medium text-fg">{c.name}</span>
                {c.description && <span className="mt-0.5 block truncate text-[0.8125rem] text-muted">{c.description}</span>}
                <span className="mt-2 block text-[0.75rem] text-faint">
                  {t("documents", { count: c.document_count })} · {t("chunks", { count: c.chunk_count, formatted: formatNumber(c.chunk_count) })} · {t("updated", { when: relativeTime(c.updated_at) })}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <CollectionDialog open={creating} onOpenChange={setCreating} onSaved={(c) => navigate(`/knowledge/${c.id}`)} />
    </PageShell>
  );
}

interface UploadState {
  name: string;
  status: "uploading" | "error";
  error?: string;
}

function CollectionDetail({ id }: { id: string }) {
  const { t } = useTranslation(["knowledge", "chat", "common"]);
  const [data, setData] = useState<(Collection & { documents: KnowledgeDocument[] }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const refresh = useCallback(() => api.collection(id).then(setData).catch((e) => setError(errorText(e))), [id]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const upload = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      setUploads((u) => [...u, { name: file.name, status: "uploading" }]);
      try {
        const doc = await api.addDocument(id, file);
        setUploads((u) => u.filter((x) => x.name !== file.name));
        if (doc.warning) toast(t("embeddingWarning", { name: file.name }), "error");
        await refresh();
      } catch (err) {
        setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, status: "error", error: errorText(err) } : x)));
      }
    }
  };

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      setHits(await api.searchCollection(id, query));
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setSearching(false);
    }
  };

  if (error)
    return (
      <PageShell>
        <p className="text-muted">{error}</p>
        <Link to="/knowledge" className="mt-3 inline-block text-accent-text hover:underline">
          {t("back")}
        </Link>
      </PageShell>
    );
  if (!data)
    return (
      <PageShell>
        <Spinner />
      </PageShell>
    );

  return (
    <PageShell>
      <Link to="/knowledge" className="mb-4 inline-flex items-center gap-1.5 text-[0.8125rem] text-muted hover:text-fg">
        <ArrowLeft className="size-3.5" /> {t("title")}
      </Link>
      <PageHeader
        title={data.name}
        description={data.description || `${t("documents", { count: data.document_count })} · ${t("chunks", { count: data.chunk_count, formatted: formatNumber(data.chunk_count) })}`}
        actions={
          <>
            <MenuRoot>
              <MenuTrigger asChild>
                <IconButton label={t("options")}>
                  <MoreHorizontal className="size-4" />
                </IconButton>
              </MenuTrigger>
              <MenuContent align="end">
                <MenuItem icon={<Pencil />} onSelect={() => setEditing(true)}>
                  {t("common:edit")}
                </MenuItem>
                <MenuItem icon={<Trash2 />} destructive onSelect={() => setConfirmDelete(true)}>
                  {t("deleteCollection")}
                </MenuItem>
              </MenuContent>
            </MenuRoot>
            <Button variant="primary" icon={<Upload className="size-4" />} onClick={() => fileRef.current?.click()}>
              {t("addDocuments")}
            </Button>
          </>
        }
      />
      <input
        ref={fileRef}
        type="file"
        multiple
        className="hidden"
        accept=".txt,.md,.markdown,.rst,.csv,.tsv,.json,.jsonl,.yaml,.yml,.toml,.xml,.html,.htm,.tex,.log,.pdf,.py,.js,.ts,.tsx,.jsx,.go,.rs,.java,.c,.h,.cpp,.hpp,.cs,.rb,.php,.swift,.kt,.sql,.sh"
        onChange={(e) => {
          if (e.target.files) void upload(e.target.files);
          e.target.value = "";
        }}
      />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(e.dataTransfer.files);
        }}
        className={cn("mb-10 rounded-2xl border border-line bg-elevated transition-colors", dragging && "border-accent ring-4 ring-accent-soft")}
      >
        {data.documents.length === 0 && uploads.length === 0 ? (
          <button type="button" onClick={() => fileRef.current?.click()} className="w-full px-6 py-12 text-center">
            <Upload className="mx-auto mb-3 size-5 text-faint" />
            <span className="block text-sm font-medium text-fg">{t("drop")}</span>
            <span className="mt-1 block text-[0.8125rem] text-muted">{t("dropHint")}</span>
          </button>
        ) : (
          <ul className="divide-y divide-line">
            {uploads.map((u) => (
              <li key={u.name} className="flex items-center gap-3 px-4 py-3 text-sm">
                {u.status === "uploading" ? <Spinner /> : <AlertTriangle className="size-4 text-danger" />}
                <span className="truncate text-fg">{u.name}</span>
                <span className={cn("ml-auto text-[0.8125rem]", u.status === "error" ? "text-danger" : "text-muted")}>
                  {u.status === "uploading" ? t("indexing") : u.error}
                </span>
                {u.status === "error" && (
                  <button type="button" className="text-[0.8125rem] text-muted hover:text-fg" onClick={() => setUploads((x) => x.filter((y) => y !== u))}>
                    {t("common:dismiss")}
                  </button>
                )}
              </li>
            ))}
            {data.documents.map((d) => (
              <DocumentRow key={d.id} doc={d} onChanged={refresh} />
            ))}
          </ul>
        )}
      </div>

      <Section title={t("testTitle")} description={t("testDescription")}>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("testPlaceholder")} aria-label={t("testQuery")} />
          <Button type="submit" variant="outline" loading={searching} icon={<Search className="size-4" />}>
            {t("search")}
          </Button>
        </form>
        {hits && (
          <ol className="mt-4 space-y-2">
            {hits.length === 0 && <p className="text-sm text-muted">{t("noMatches")}</p>}
            {hits.map((h, i) => (
              <li key={h.chunk_id} className="rounded-xl border border-line bg-elevated px-4 py-3">
                <div className="mb-1 flex items-center gap-2 text-[0.75rem] text-faint">
                  <span className="font-mono text-accent-text">{i + 1}</span>
                  <span className="truncate">{h.filename}</span>
                  <span>· {t("chunkN", { n: h.index + 1 })}</span>
                  <span className="ml-auto">{t(`chat:method.${h.method as "keyword" | "semantic" | "hybrid"}`)}</span>
                </div>
                <p className="line-clamp-4 text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-fg">{h.text}</p>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <CollectionDialog open={editing} onOpenChange={setEditing} collection={data} onSaved={() => void refresh()} />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title={t("deleteTitle", { name: data.name })}
        description={t("deleteBody")}
        onConfirm={async () => {
          await api.deleteCollection(id);
          navigate("/knowledge");
        }}
      />
    </PageShell>
  );
}

function DocumentRow({ doc, onChanged }: { doc: KnowledgeDocument; onChanged: () => void }) {
  const { t } = useTranslation(["knowledge", "chat", "common"]);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <FileText className="size-4 shrink-0 text-faint" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-fg">{doc.filename}</p>
        <p className="text-[0.75rem] text-faint">
          {formatBytes(doc.size)} · {t("chunks", { count: doc.chunk_count, formatted: formatNumber(doc.chunk_count) })} · {doc.embedding_model ? t("docHybrid") : t("docKeyword")}
        </p>
        {doc.warning && <p className="mt-0.5 text-[0.75rem] text-warning">{doc.warning_code ? t("embeddingsUnavailable") : doc.warning}</p>}
      </div>
      <IconButton
        label={t("reindex")}
        size="sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            const updated = await api.reindexDocument(doc.id);
            toast(updated.warning ? t("embeddingsUnavailable") : t("reindexed"), updated.warning ? "error" : "success");
            onChanged();
          } catch (err) {
            toast(errorText(err), "error");
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? <ThinkingIndicator className="text-[0.875rem]" /> : <RefreshCw className="size-3.5" />}
      </IconButton>
      <IconButton label={t("removeDocument")} size="sm" onClick={() => setConfirm(true)}>
        <Trash2 className="size-3.5" />
      </IconButton>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={t("removeTitle", { name: doc.filename })}
        confirmLabel={t("remove")}
        onConfirm={async () => {
          await api.deleteDocument(doc.id);
          onChanged();
        }}
      />
    </li>
  );
}
