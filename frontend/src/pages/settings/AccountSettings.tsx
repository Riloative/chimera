import { Download, Laptop, LogOut } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { LanguageSelect } from "@/components/LanguageSelect";
import { Button, Dialog, Field, Input, LoadState } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useLoad } from "@/lib/useLoad";
import { errorText } from "@/lib/errors";
import { formatDateTime, relativeTime } from "@/lib/time";
import { downloadUrl } from "@/lib/utils";
import { PasswordHint, PasswordInput } from "@/pages/AuthPages";
import { Group } from "@/components/Page";
import { useAuth } from "@/stores/auth";
import { toast } from "@/stores/ui";

/** A short, human summary of a User-Agent string (no fingerprinting detail). */
export function describeAgent(ua: string | null): string {
  if (!ua) return "";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "";
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return [browser, os].filter(Boolean).join(" · ");
}

function fieldError(err: unknown, field: string): string | null {
  return err instanceof ApiError && err.params?.field === field ? errorText(err) : null;
}

export function AccountSettings() {
  const { t } = useTranslation(["account", "common"]);
  return (
    <>
      <Profile />
      <Group title={t("language.title")} description={t("language.help")}>
        <LanguageSelect className="-ml-1.5" compact />
      </Group>
      <PasswordForm />
      <Sessions />
      <Group title={t("export.title")} description={t("export.help")}>
        <Button variant="outline" icon={<Download className="size-4" />} onClick={() => downloadUrl("/api/data/export")} className="self-start">
          {t("export.button")}
        </Button>
      </Group>
      <DeleteAccount />
    </>
  );
}

function Profile() {
  const { t } = useTranslation(["account", "common"]);
  const { user, setUser } = useAuth();
  const [username, setUsername] = useState(user?.username ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!user) return null;
  const changed = username.trim() !== user.username || email.trim() !== user.email;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setUser(await api.updateAccount({ username: username.trim(), email: email.trim(), current_password: password }));
      setPassword("");
      toast(t("profile.saved"), "success");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const general = error && !fieldError(error, "username") && !fieldError(error, "email") && !fieldError(error, "current_password") ? errorText(error) : null;

  return (
    <Group title={t("profile.title")} description={user.role === "admin" ? t("profile.adminNote") : undefined}>
      <form className="space-y-4" onSubmit={submit}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("profile.username")} htmlFor="acc-username" error={fieldError(error, "username")}>
            <Input id="acc-username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={32} />
          </Field>
          <Field label={t("profile.email")} htmlFor="acc-email" error={fieldError(error, "email")}>
            <Input id="acc-email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} maxLength={254} />
          </Field>
        </div>
        {changed && (
          <Field label={t("profile.currentPassword")} htmlFor="acc-current" hint={t("profile.passwordNeeded")} error={fieldError(error, "current_password")}>
            <PasswordInput id="acc-current" value={password} onChange={setPassword} autoComplete="current-password" />
          </Field>
        )}
        {general && <p className="text-ui text-danger">{general}</p>}
        <Button type="submit" variant="primary" disabled={!changed || !password} loading={busy}>
          {t("profile.save")}
        </Button>
      </form>
    </Group>
  );
}

function PasswordForm() {
  const { t } = useTranslation(["account", "common"]);
  const setUser = useAuth((s) => s.setUser);
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (form.next !== form.confirm) {
      setError(new ApiError("mismatch", 422, "password_mismatch", { field: "new_password_confirm" }));
      return;
    }
    setBusy(true);
    try {
      setUser(await api.changePassword({ current_password: form.current, new_password: form.next, new_password_confirm: form.confirm }));
      setForm({ current: "", next: "", confirm: "" });
      toast(t("password.changed"), "success");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Group title={t("password.title")} description={t("password.help")}>
      <form className="space-y-4" onSubmit={submit}>
        <Field label={t("password.current")} htmlFor="pw-current" error={fieldError(error, "current_password")}>
          <PasswordInput id="pw-current" value={form.current} onChange={(v) => setForm({ ...form, current: v })} autoComplete="current-password" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("password.new")} htmlFor="pw-new" error={fieldError(error, "new_password") ?? fieldError(error, "password")} hint={<PasswordHint password={form.next} />}>
            <PasswordInput id="pw-new" value={form.next} onChange={(v) => setForm({ ...form, next: v })} autoComplete="new-password" />
          </Field>
          <Field label={t("password.confirm")} htmlFor="pw-confirm" error={fieldError(error, "new_password_confirm")}>
            <PasswordInput id="pw-confirm" value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} autoComplete="new-password" />
          </Field>
        </div>
        {error && !(error instanceof ApiError && error.params?.field) ? <p className="text-ui text-danger">{errorText(error)}</p> : null}
        <Button type="submit" variant="primary" loading={busy} disabled={!form.current || !form.next}>
          {t("password.submit")}
        </Button>
      </form>
    </Group>
  );
}

function Sessions() {
  const { t } = useTranslation(["account", "common"]);
  const { data: items, error, reload: load } = useLoad(api.sessions);

  return (
    <Group title={t("sessions.title")} description={t("sessions.help")}>
      {items === null ? (
        <LoadState error={error} onRetry={() => void load()} />
      ) : (
        <>
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-elevated">
            {items.map((s) => (
              <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                <Laptop className="size-4 shrink-0 text-faint" aria-hidden />
                <div className="min-w-0 flex-1 text-ui">
                  <p className="truncate text-fg">
                    {describeAgent(s.user_agent) || t("sessions.unknownDevice")}
                    {s.current && <span className="ml-2 rounded-md bg-accent-soft px-1.5 py-0.5 text-caption text-accent-text">{t("sessions.current")}</span>}
                  </p>
                  <p className="truncate text-caption text-muted">
                    {t("sessions.meta", { created: formatDateTime(s.created_at), used: relativeTime(s.last_used_at) })}
                    {s.ip ? ` · ${s.ip}` : ""}
                  </p>
                </div>
                {!s.current && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      try {
                        await api.revokeSession(s.id);
                        await load();
                        toast(t("sessions.revoked"));
                      } catch (err) {
                        toast(errorText(err), "error");
                      }
                    }}
                  >
                    {t("sessions.revoke")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {items.length > 1 && (
            <Button
              variant="outline"
              icon={<LogOut className="size-4" />}
              onClick={async () => {
                try {
                  const r = await api.revokeOtherSessions();
                  await load();
                  toast(t("sessions.revokedOthers", { count: r.revoked }));
                } catch (err) {
                  toast(errorText(err), "error");
                }
              }}
            >
              {t("sessions.revokeOthers")}
            </Button>
          )}
        </>
      )}
    </Group>
  );
}

function DeleteAccount() {
  const { t } = useTranslation(["account", "common"]);
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <Group title={t("delete.title")} description={t("delete.help")}>
      <Button variant="outline" className="self-start text-danger" onClick={() => setOpen(true)}>
        {t("delete.button")}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) {
            setPassword("");
            setError(null);
          }
        }}
        title={t("delete.confirmTitle")}
        description={t("delete.confirmBody")}
        size="sm"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button
              variant="danger"
              loading={busy}
              disabled={!password}
              onClick={async () => {
                setBusy(true);
                setError(null);
                try {
                  await api.deleteAccount(password);
                  window.location.assign("/login");
                } catch (err) {
                  setError(errorText(err));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("delete.confirm")}
            </Button>
          </>
        }
      >
        <div className="space-y-2 pb-3">
          <Field label={t("delete.password")} htmlFor="del-password" error={error}>
            <PasswordInput id="del-password" value={password} onChange={setPassword} autoComplete="current-password" autoFocus />
          </Field>
        </div>
      </Dialog>
    </Group>
  );
}
