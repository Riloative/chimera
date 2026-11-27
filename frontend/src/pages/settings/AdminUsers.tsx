import { Copy, KeyRound, MoreHorizontal, Plus, Shield, ShieldOff, Trash2, UserCheck, UserX } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, ConfirmDialog, Dialog, Field, Input, MenuContent, MenuItem, MenuRoot, MenuSeparator, MenuTrigger, LoadState, Select, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";
import { errorText } from "@/lib/errors";
import { relativeTime } from "@/lib/time";
import type { AdminSettings, User } from "@/lib/types";
import { cn, copyText } from "@/lib/utils";
import { Group } from "@/components/Page";
import { useAuth } from "@/stores/auth";
import { toast } from "@/stores/ui";

export function AdminUsers() {
  const { t } = useTranslation(["admin", "common"]);
  const me = useAuth((s) => s.user);
  const [allowlist, setAllowlist] = useState("");
  const { data: users, error: usersError, reload: load } = useLoad(api.adminUsers);
  const {
    data: settings,
    setData: setSettings,
    error: settingsError,
    reload: reloadSettings,
  } = useLoad(async () => {
    const s = await api.adminSettings();
    setAllowlist(s.provider_url_allowlist.join("\n"));
    return s;
  });
  const [creating, setCreating] = useState(false);
  const [temporary, setTemporary] = useState<{ username: string; password: string } | null>(null);
  const [deleting, setDeleting] = useState<User | null>(null);


  const saveSettings = async (patch: Partial<AdminSettings>) => {
    try {
      setSettings(await api.adminUpdateSettings(patch));
      toast(t("saved"), "success");
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const update = async (u: User, data: { role?: "admin" | "user"; active?: boolean }) => {
    try {
      await api.adminUpdateUser(u.id, data);
      await load();
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  return (
    <>
      <Group title={t("registration.title")} description={t("registration.help")}>
        {settings ? (
          <Field label={t("registration.mode")} htmlFor="reg-mode" hint={t(`registration.${settings.registration_mode}Help`)}>
            <Select id="reg-mode" value={settings.registration_mode} onChange={(e) => saveSettings({ registration_mode: e.target.value as AdminSettings["registration_mode"] })} className="max-w-sm">
              <option value="first_user_only">{t("registration.first_user_only")}</option>
              <option value="closed">{t("registration.closed")}</option>
              <option value="open">{t("registration.open")}</option>
            </Select>
          </Field>
        ) : (
          <LoadState error={settingsError} onRetry={() => void reloadSettings()} />
        )}
      </Group>

      <Group title={t("users.title")} description={t("users.help")}>
        <div className="flex justify-end">
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>
            {t("users.create")}
          </Button>
        </div>
        {users === null ? (
          <LoadState error={usersError} onRetry={() => void load()} />
        ) : (
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-elevated">
            {users.map((u) => (
              <li key={u.id} className="flex items-center gap-3 px-4 py-3">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-subtle text-caption font-semibold text-fg" aria-hidden>
                  {u.username.slice(0, 2).toUpperCase()}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-x-2 text-sm">
                    <span className={cn("truncate font-medium", u.active ? "text-fg" : "text-muted line-through")}>{u.username}</span>
                    {u.role === "admin" && <span className="rounded-md bg-accent-soft px-1.5 py-0.5 text-caption text-accent-text">{t("users.admin")}</span>}
                    {!u.active && <span className="rounded-md bg-subtle px-1.5 py-0.5 text-caption text-muted">{t("users.disabled")}</span>}
                    {u.must_change_password && <span className="rounded-md bg-subtle px-1.5 py-0.5 text-caption text-muted">{t("users.mustChange")}</span>}
                    {u.id === me?.id && <span className="text-caption text-faint">{t("users.you")}</span>}
                  </p>
                  <p className="truncate text-caption text-muted">
                    {u.email} · {u.last_login_at ? t("users.lastLogin", { when: relativeTime(u.last_login_at) }) : t("users.neverLoggedIn")}
                  </p>
                </div>
                {u.id !== me?.id && (
                  <MenuRoot>
                    <MenuTrigger asChild>
                      <button type="button" aria-label={t("users.options", { username: u.username })} className="flex size-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-fg">
                        <MoreHorizontal className="size-4" />
                      </button>
                    </MenuTrigger>
                    <MenuContent align="end">
                      <MenuItem icon={u.role === "admin" ? <ShieldOff /> : <Shield />} onSelect={() => update(u, { role: u.role === "admin" ? "user" : "admin" })}>
                        {u.role === "admin" ? t("users.makeUser") : t("users.makeAdmin")}
                      </MenuItem>
                      <MenuItem icon={u.active ? <UserX /> : <UserCheck />} onSelect={() => update(u, { active: !u.active })}>
                        {u.active ? t("users.disable") : t("users.enable")}
                      </MenuItem>
                      <MenuItem
                        icon={<KeyRound />}
                        onSelect={async () => {
                          try {
                            const r = await api.adminResetPassword(u.id);
                            setTemporary({ username: u.username, password: r.temporary_password });
                            await load();
                          } catch (err) {
                            toast(errorText(err), "error");
                          }
                        }}
                      >
                        {t("users.resetPassword")}
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem icon={<Trash2 />} destructive onSelect={() => setDeleting(u)}>
                        {t("users.delete")}
                      </MenuItem>
                    </MenuContent>
                  </MenuRoot>
                )}
              </li>
            ))}
          </ul>
        )}
      </Group>

      <Group title={t("urlPolicy.title")} description={t("urlPolicy.help")}>
        {settings && (
          <div className="space-y-3">
            <Field label={t("urlPolicy.label")} htmlFor="url-policy" hint={t(`urlPolicy.${settings.provider_url_policy}Help`)}>
              <Select id="url-policy" value={settings.provider_url_policy} onChange={(e) => saveSettings({ provider_url_policy: e.target.value as AdminSettings["provider_url_policy"] })} className="max-w-sm">
                <option value="private">{t("urlPolicy.private")}</option>
                <option value="custom">{t("urlPolicy.custom")}</option>
                <option value="any">{t("urlPolicy.any")}</option>
              </Select>
            </Field>
            {settings.provider_url_policy === "custom" && (
              <Field label={t("urlPolicy.allowlist")} htmlFor="url-allowlist" hint={t("urlPolicy.allowlistHelp")}>
                <Textarea id="url-allowlist" rows={4} value={allowlist} onChange={(e) => setAllowlist(e.target.value)} onBlur={() => saveSettings({ provider_url_allowlist: allowlist.split("\n") })} className="font-mono" />
              </Field>
            )}
          </div>
        )}
      </Group>

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(u, password) => {
          void load();
          if (password) setTemporary({ username: u.username, password });
        }}
      />
      <TemporaryPasswordDialog value={temporary} onClose={() => setTemporary(null)} />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={t("users.deleteTitle", { username: deleting?.username ?? "" })}
        description={t("users.deleteBody")}
        onConfirm={async () => {
          if (!deleting) return;
          try {
            await api.adminDeleteUser(deleting.id);
            await load();
          } catch (err) {
            toast(errorText(err), "error");
          }
        }}
      />
    </>
  );
}

function CreateUserDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (u: User, temporaryPassword: string | null) => void }) {
  const { t } = useTranslation(["admin", "common"]);
  const [form, setForm] = useState({ email: "", username: "", password: "", role: "user" as "user" | "admin" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setForm({ email: "", username: "", password: "", role: "user" });
      setError(null);
    }
  }, [open]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.adminCreateUser({ email: form.email.trim(), username: form.username.trim(), role: form.role, password: form.password || undefined });
      onCreated(created, created.temporary_password);
      onOpenChange(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("create.title")}
      description={t("create.help")}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={busy} disabled={!form.email.trim() || !form.username.trim()}>
            {t("create.submit")}
          </Button>
        </>
      }
    >
      <form
        className="space-y-4 pb-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <Field label={t("create.email")} htmlFor="nu-email">
          <Input id="nu-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} autoFocus autoComplete="off" />
        </Field>
        <Field label={t("create.username")} htmlFor="nu-username">
          <Input id="nu-username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} autoComplete="off" maxLength={32} />
        </Field>
        <Field label={t("create.password")} htmlFor="nu-password" hint={t("create.passwordHint")}>
          <Input id="nu-password" type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" />
        </Field>
        <Field label={t("create.role")} htmlFor="nu-role">
          <Select id="nu-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as "user" | "admin" })}>
            <option value="user">{t("create.roleUser")}</option>
            <option value="admin">{t("create.roleAdmin")}</option>
          </Select>
        </Field>
        {error && <p className="text-ui text-danger">{error}</p>}
        <button type="submit" className="hidden" />
      </form>
    </Dialog>
  );
}

function TemporaryPasswordDialog({ value, onClose }: { value: { username: string; password: string } | null; onClose: () => void }) {
  const { t } = useTranslation(["admin", "common"]);
  return (
    <Dialog
      open={!!value}
      onOpenChange={(o) => !o && onClose()}
      title={t("temporary.title")}
      description={t("temporary.body", { username: value?.username ?? "" })}
      size="sm"
      footer={
        <Button variant="primary" onClick={onClose}>
          {t("temporary.done")}
        </Button>
      }
    >
      <div className="mb-3 flex items-center gap-2 rounded-lg bg-subtle px-3 py-2.5">
        <code className="flex-1 font-mono text-sm break-all text-fg">{value?.password}</code>
        <Button
          size="sm"
          variant="ghost"
          icon={<Copy className="size-3.5" />}
          onClick={async () => value && (await copyText(value.password)) && toast(t("temporary.copied"))}
        >
          {t("temporary.copy")}
        </Button>
      </div>
    </Dialog>
  );
}
