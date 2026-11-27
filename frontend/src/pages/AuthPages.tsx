import { Eye, EyeOff } from "lucide-react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { LanguageSelect } from "@/components/LanguageSelect";
import { LogoMark } from "@/components/Logo";
import { Button, Input } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { errorText } from "@/lib/errors";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth";

/** Only same-app paths are allowed as post-login destinations. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") && !next.startsWith("/register") ? next : "/";
}

export function AuthShell({ title, subtitle, children, footer }: { title: string; subtitle?: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col bg-bg">
      <div className="flex justify-end px-4 pt-4">
        <LanguageSelect />
      </div>
      <main className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm">
          <LogoMark className="mx-auto mb-5 size-10 text-accent" />
          <h1 className="mb-1 text-center text-xl font-medium text-fg">{title}</h1>
          {subtitle && <p className="mb-6 text-center text-sm leading-relaxed text-muted">{subtitle}</p>}
          {children}
          {footer && <div className="mt-6 text-center text-sm text-muted">{footer}</div>}
        </div>
      </main>
    </div>
  );
}

function FieldRow({ id, label, error, hint, children }: { id: string; label: string; error?: string | null; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-ui font-medium text-fg">
        {label}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} className="text-xs text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs leading-relaxed text-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function PasswordInput({
  id,
  value,
  onChange,
  autoComplete,
  invalid,
  autoFocus,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: "current-password" | "new-password";
  invalid?: boolean;
  autoFocus?: boolean;
}) {
  const { t } = useTranslation(["auth", "errors"]);
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <Input
        id={id}
        name={id}
        type={visible ? "text" : "password"}
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? `${id}-error` : undefined}
        autoFocus={autoFocus}
        className="h-10 pr-10"
        maxLength={128}
      />
      <button
        type="button"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? t("hidePassword") : t("showPassword")}
        aria-pressed={visible}
        className="absolute top-1/2 right-1.5 flex size-7 -translate-y-1/2 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-fg"
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

/** A restrained strength hint: length is what matters most. */
export function PasswordHint({ password }: { password: string }) {
  const { t } = useTranslation(["auth", "errors"]);
  if (!password) return <>{t("passwordRule")}</>;
  const variety = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(password)).length;
  const level = password.length < 10 ? "short" : password.length >= 16 || (password.length >= 12 && variety >= 3) ? "strong" : "fair";
  return (
    <span className={cn(level === "short" ? "text-muted" : level === "strong" ? "text-success" : "text-muted")}>
      {t(`strength.${level}`)}
    </span>
  );
}

function useFieldErrors() {
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const capture = (err: unknown) => {
    const field = err instanceof ApiError ? (err.params?.field as string | undefined) : undefined;
    if (field) {
      setErrors({ [field]: errorText(err) });
      setFormError(null);
    } else {
      setErrors({});
      setFormError(errorText(err));
    }
  };
  const reset = () => {
    setErrors({});
    setFormError(null);
  };
  return { errors, formError, capture, reset, setErrors };
}

export function LoginPage() {
  const { t } = useTranslation(["auth", "errors"]);
  const login = useAuth((s) => s.login);
  const registrationOpen = useAuth((s) => s.registrationOpen);
  const needsSetup = useAuth((s) => s.needsSetup);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const { formError, capture, reset } = useFieldErrors();

  useEffect(() => {
    document.title = `${t("signIn")} · Chimera`;
  }, [t]);

  if (needsSetup) {
    return <Navigate to={`/register${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`} replace />;
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    reset();
    try {
      await login(identifier, password, remember);
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (err) {
      capture(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title={t("welcomeBack")}
      subtitle={t("signInSubtitle")}
      footer={
        registrationOpen ? (
          <>
            {t("noAccount")}{" "}
            <Link to={`/register${params.get("next") ? `?next=${encodeURIComponent(params.get("next")!)}` : ""}`} className="link">
              {t("register")}
            </Link>
          </>
        ) : (
          t("askAdmin")
        )
      }
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <FieldRow id="identifier" label={t("identifier")}>
          <Input
            id="identifier"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            autoFocus
            className="h-10"
            maxLength={254}
          />
        </FieldRow>
        <FieldRow id="password" label={t("password")}>
          <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="current-password" />
        </FieldRow>
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-[var(--accent)]" />
          {t("rememberMe")}
        </label>
        {formError && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-ui text-danger">
            {formError}
          </p>
        )}
        <Button type="submit" variant="primary" className="h-10 w-full" loading={busy} disabled={!identifier.trim() || !password}>
          {t("signIn")}
        </Button>
      </form>
    </AuthShell>
  );
}

export function RegisterPage() {
  const { t } = useTranslation(["auth", "errors"]);
  const register = useAuth((s) => s.register);
  const { needsSetup, registrationOpen, setupTokenRequired } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [form, setForm] = useState({ email: "", username: "", password: "", confirm: "", token: "" });
  const [remember, setRemember] = useState(false);
  const [busy, setBusy] = useState(false);
  const { errors, formError, capture, reset, setErrors } = useFieldErrors();
  const set = (k: keyof typeof form) => (v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    document.title = `${t("register")} · Chimera`;
  }, [t]);

  if (!registrationOpen) {
    return (
      <AuthShell
        title={t("registrationClosedTitle")}
        subtitle={t("registrationClosedBody")}
        footer={
          <Link to="/login" className="link">
            {t("backToSignIn")}
          </Link>
        }
      >
        {null}
      </AuthShell>
    );
  }

  const validate = (): boolean => {
    const next: Record<string, string> = {};
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(form.email.trim())) next.email = t("errors:email_invalid");
    if (!/^[A-Za-z0-9._-]{3,32}$/.test(form.username.trim())) next.username = t("errors:username_invalid");
    if (form.password.length < 10) next.password = t("errors:password_too_short", { min: 10 });
    else if (form.password !== form.confirm) next.password_confirm = t("errors:password_mismatch");
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    reset();
    if (!validate()) return;
    setBusy(true);
    try {
      await register({
        email: form.email.trim(),
        username: form.username.trim(),
        password: form.password,
        password_confirm: form.confirm,
        setup_token: form.token || undefined,
        remember,
      });
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (err) {
      capture(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title={needsSetup ? t("setupTitle") : t("createAccount")}
      subtitle={needsSetup ? t("setupSubtitle") : t("registerSubtitle")}
      footer={
        needsSetup ? null : (
          <>
            {t("haveAccount")}{" "}
            <Link to="/login" className="link">
              {t("signIn")}
            </Link>
          </>
        )
      }
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <FieldRow id="email" label={t("email")} error={errors.email}>
          <Input id="email" name="email" type="email" autoComplete="email" value={form.email} onChange={(e) => set("email")(e.target.value)} autoFocus className="h-10" aria-invalid={!!errors.email || undefined} maxLength={254} />
        </FieldRow>
        <FieldRow id="username" label={t("username")} error={errors.username} hint={t("usernameRule")}>
          <Input
            id="username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={form.username}
            onChange={(e) => set("username")(e.target.value)}
            className="h-10"
            aria-invalid={!!errors.username || undefined}
            maxLength={32}
          />
        </FieldRow>
        <FieldRow id="new-password" label={t("password")} error={errors.password} hint={<PasswordHint password={form.password} />}>
          <PasswordInput id="new-password" value={form.password} onChange={set("password")} autoComplete="new-password" invalid={!!errors.password} />
        </FieldRow>
        <FieldRow id="password-confirm" label={t("confirmPassword")} error={errors.password_confirm}>
          <PasswordInput id="password-confirm" value={form.confirm} onChange={set("confirm")} autoComplete="new-password" invalid={!!errors.password_confirm} />
        </FieldRow>
        {needsSetup && setupTokenRequired && (
          <FieldRow id="setup-token" label={t("setupToken")} error={errors.setup_token} hint={t("setupTokenHint")}>
            <Input id="setup-token" value={form.token} onChange={(e) => set("token")(e.target.value)} className="h-10 font-mono" autoComplete="off" />
          </FieldRow>
        )}
        <label className="flex items-center gap-2 text-sm text-muted">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-[var(--accent)]" />
          {t("rememberMe")}
        </label>
        {formError && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-ui text-danger">
            {formError}
          </p>
        )}
        <Button type="submit" variant="primary" className="h-10 w-full" loading={busy}>
          {needsSetup ? t("createAdmin") : t("register")}
        </Button>
      </form>
    </AuthShell>
  );
}

/** Shown after an admin reset: a new password is required before anything else. */
export function ChangePasswordScreen() {
  const { t } = useTranslation(["auth", "errors"]);
  const { user, setUser, logout } = useAuth();
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const { errors, formError, capture, reset } = useFieldErrors();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    reset();
    setBusy(true);
    try {
      setUser(await api.changePassword({ current_password: form.current, new_password: form.next, new_password_confirm: form.confirm }));
      window.location.assign("/");
    } catch (err) {
      capture(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthShell
      title={t("mustChangeTitle")}
      subtitle={t("mustChangeSubtitle", { username: user?.username ?? "" })}
      footer={
        <button type="button" className="link" onClick={() => logout()}>
          {t("signOut")}
        </button>
      }
    >
      <form className="space-y-4" onSubmit={submit} noValidate>
        <FieldRow id="current-password" label={t("temporaryPassword")} error={errors.current_password}>
          <PasswordInput id="current-password" value={form.current} onChange={(v) => setForm({ ...form, current: v })} autoComplete="current-password" autoFocus />
        </FieldRow>
        <FieldRow id="new-password" label={t("newPassword")} error={errors.new_password} hint={<PasswordHint password={form.next} />}>
          <PasswordInput id="new-password" value={form.next} onChange={(v) => setForm({ ...form, next: v })} autoComplete="new-password" />
        </FieldRow>
        <FieldRow id="confirm-password" label={t("confirmPassword")} error={errors.new_password_confirm}>
          <PasswordInput id="confirm-password" value={form.confirm} onChange={(v) => setForm({ ...form, confirm: v })} autoComplete="new-password" />
        </FieldRow>
        {formError && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-ui text-danger">
            {formError}
          </p>
        )}
        <Button type="submit" variant="primary" className="h-10 w-full" loading={busy} disabled={!form.current || !form.next}>
          {t("setPassword")}
        </Button>
      </form>
    </AuthShell>
  );
}
