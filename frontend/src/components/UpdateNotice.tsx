import { ArrowUpCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
import { create } from "zustand";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { errorText } from "@/lib/errors";
import type { UpdateView } from "@/lib/types";
import { useAuth } from "@/stores/auth";
import { toast } from "@/stores/ui";

export const UPDATE_DOCS = "https://github.com/Riloative/chimera/blob/main/docs/DEPLOYMENT.md#updating";
const DISMISSED = "chimera.update.dismissed";

function dismissedVersion(): string | null {
  try {
    return localStorage.getItem(DISMISSED);
  } catch {
    return null;
  }
}

interface UpdatesState {
  view: UpdateView | null;
  /** The check has answered (or given up): the page can render with or without the notice. */
  settled: boolean;
  load: () => Promise<void>;
  setView: (view: UpdateView) => void;
}

/** How long the page waits for the update check before rendering without it. */
const WAIT_MS = 800;

export const useUpdates = create<UpdatesState>((set) => ({
  view: null,
  settled: false,
  load: async () => {
    const timer = setTimeout(() => set({ settled: true }), WAIT_MS);
    try {
      set({ view: await api.adminUpdates() });
    } catch {
      /* not shown */
    } finally {
      clearTimeout(timer);
      set({ settled: true });
    }
  },
  setView: (view) => set({ view }),
}));

/** For administrators the main area renders once the update check has answered, so the
 * notice is part of the first frame instead of pushing the page down later (layout shift). */
export function useUpdatesReady(): boolean {
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const settled = useUpdates((s) => s.settled);
  useEffect(() => {
    if (isAdmin && !useUpdates.getState().settled) void useUpdates.getState().load();
  }, [isAdmin]);
  return !isAdmin || settled;
}

/** Administrators only: asks once whether to check for updates, then shows a
 * quiet "new version available" line. Nothing is ever installed from here. */
export function UpdateNotice() {
  const { t } = useTranslation("settings");
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const view = useUpdates((s) => s.view);
  const setView = useUpdates((s) => s.setView);
  const [dismissed, setDismissed] = useState(dismissedVersion);

  if (!isAdmin || !view) return null;

  const answer = async (enabled: boolean) => {
    try {
      setView(await api.setUpdateCheck(enabled));
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  if (!view.asked) {
    return (
      <div role="region" aria-label={t("updates.askTitle")} className="mx-3 mt-3 rounded-xl border border-line bg-elevated px-4 py-3 text-ui md:mx-4" data-testid="update-ask">
        <p className="font-medium text-fg">{t("updates.askTitle")}</p>
        <p className="mt-1 leading-relaxed text-muted">{t("updates.askBody")}</p>
        <div className="mt-2.5 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" onClick={() => answer(true)}>
            {t("updates.askYes")}
          </Button>
          <Button size="sm" variant="outline" onClick={() => answer(false)}>
            {t("updates.askNo")}
          </Button>
        </div>
      </div>
    );
  }

  const available = view.available;
  if (!available || dismissed === available.version) return null;
  const dismiss = () => {
    try {
      localStorage.setItem(DISMISSED, available.version);
    } catch {
      /* private mode: dismissed for this page view only */
    }
    setDismissed(available.version);
  };
  return (
    <div role="status" className="mx-3 mt-3 flex items-start gap-3 rounded-xl border border-line bg-elevated px-4 py-2.5 text-ui md:mx-4" data-testid="update-notice">
      <ArrowUpCircle className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
      <p className="min-w-0 flex-1 leading-relaxed text-fg">
        {t("updates.available", { version: available.version })}{" "}
        <a href={available.url} target="_blank" rel="noopener noreferrer" className="link">
          {t("updates.releaseNotes")}
        </a>
        {" · "}
        <a href={UPDATE_DOCS} target="_blank" rel="noopener noreferrer" className="link">
          {t("updates.howTo")}
        </a>
      </p>
      <button type="button" onClick={dismiss} aria-label={t("updates.dismiss")} className="-my-1 -mr-1.5 flex size-7 shrink-0 items-center justify-center rounded-md text-muted hover:bg-hover hover:text-fg">
        <X className="size-4" />
      </button>
    </div>
  );
}
