import { Info, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { api } from "@/lib/api";
import { formatList } from "@/lib/utils";
import { useAuth } from "@/stores/auth";

type Kind = "conversations" | "prompts" | "files" | "collections" | "providers" | "settings";

/** One-time notice: the first administrator inherited data from before accounts existed. */
export function LegacyNotice() {
  const { t } = useTranslation("account");
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const notice = user?.legacy_notice;
  if (!user || !notice || Object.keys(notice).length === 0) return null;

  const parts = (Object.entries(notice) as [Kind, number][])
    .filter(([kind]) => ["conversations", "prompts", "files", "collections", "providers", "settings"].includes(kind))
    .map(([kind, count]) => (kind === "settings" ? t("legacy.settings") : t(`legacy.${kind}`, { count })));

  const dismiss = async () => {
    setUser({ ...user, legacy_notice: null });
    await api.dismissNotice().catch(() => undefined);
  };

  return (
    <div role="status" className="mx-3 mt-3 flex items-start gap-3 rounded-xl border border-line bg-elevated px-4 py-3 text-ui md:mx-4">
      <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
      <p className="flex-1 leading-relaxed text-fg">{t("legacy.body", { items: formatList(parts) })}</p>
      <button type="button" onClick={dismiss} aria-label={t("legacy.dismiss")} className="text-muted hover:text-fg">
        <X className="size-4" />
      </button>
    </div>
  );
}
