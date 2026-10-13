import { useState } from "react";
import { LogoMark } from "@/components/Logo";
import { Button, Input } from "@/components/ui";
import { api } from "@/lib/api";

export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <div className="flex min-h-full items-center justify-center bg-bg px-4">
      <form
        className="w-full max-w-sm"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await api.login(password);
            onSuccess();
          } catch (err) {
            setError((err as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <LogoMark className="mx-auto mb-5 size-10 text-accent" />
        <h1 className="mb-1 text-center text-xl font-medium text-fg">Welcome back</h1>
        <p className="mb-6 text-center text-sm text-muted">This Chimera workspace is password protected.</p>
        <label htmlFor="password" className="sr-only">
          Password
        </label>
        <Input id="password" type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" className="h-10" />
        {error && (
          <p role="alert" className="mt-2 text-[0.8125rem] text-danger">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" className="mt-3 h-10 w-full" loading={busy} disabled={!password}>
          Sign in
        </Button>
      </form>
    </div>
  );
}
