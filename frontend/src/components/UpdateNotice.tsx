import { ArrowUpCircle, X } from "lucide-react";
import { useEffect, useState } from "react";
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

/** Administrators only: asks once whether to check for updates, then shows a
 * quiet "new version available" line. Nothing is ever installed from here. */
export function UpdateNotice() {
  const { t } = useTranslation("settings");
  const isAdmin = useAuth((s) => s.user?.role === "admin");
  const [view, setView] = useState<UpdateView | null>(null);
  const [dismissed, setDismissed] = useState(dismissedVersion);

  useEffect(() => {
    if (!isAdmin) return;
    api.adminUpdates().then(setView).catch(() => undefined);
  }, [isAdmin]);

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
      <div role="region" aria-label={t("updates.askTitle")} className="mx-3 mt-3 rounded-xl border border-line bg-elevated px-4 py-3 text-[0.8125rem] md:mx-4" data-testid="update-ask">
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
    <div role="status" className="mx-3 mt-3 flex items-start gap-3 rounded-xl border border-line bg-elevated px-4 py-2.5 text-[0.8125rem] md:mx-4" data-testid="update-notice">
      <ArrowUpCircle className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
      <p className="min-w-0 flex-1 leading-relaxed text-fg">
        {t("updates.available", { version: available.version })}{" "}
        <a href={available.url} target="_blank" rel="noopener noreferrer" className="text-accent-text hover:underline">
          {t("updates.releaseNotes")}
        </a>
        {" · "}
        <a href={UPDATE_DOCS} target="_blank" rel="noopener noreferrer" className="text-accent-text hover:underline">
          {t("updates.howTo")}
        </a>
      </p>
      <button type="button" onClick={dismiss} aria-label={t("updates.dismiss")} className="text-muted hover:text-fg">
        <X className="size-4" />
      </button>
    </div>
  );
}
