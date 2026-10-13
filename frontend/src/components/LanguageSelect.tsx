import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LANGUAGES, setLanguage, type Language } from "@/i18n";
import { LANGUAGE_NAMES } from "@/i18n/resources";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAuth } from "@/stores/auth";

/** Quiet language switcher. Saved to the account when signed in, locally otherwise. */
export function LanguageSelect({ className, compact }: { className?: string; compact?: boolean }) {
  const { t, i18n } = useTranslation();
  const signedIn = useAuth((s) => s.status === "authenticated");
  const setUser = useAuth((s) => s.setUser);

  const change = async (lng: Language) => {
    await setLanguage(lng);
    if (signedIn) {
      try {
        setUser(await api.updateAccount({ language: lng }));
      } catch {
        /* the local choice still applies */
      }
    }
  };

  return (
    <label className={cn("inline-flex items-center gap-1.5 text-[0.8125rem] text-muted", className)}>
      <Languages className="size-4 shrink-0" aria-hidden />
      <span className={compact ? "sr-only" : "sr-only sm:not-sr-only"}>{t("language")}</span>
      <select
        value={i18n.language}
        onChange={(e) => change(e.target.value as Language)}
        aria-label={t("language")}
        className="h-8 cursor-pointer rounded-lg bg-transparent px-1.5 text-[0.8125rem] text-fg hover:bg-hover focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-soft"
      >
        {LANGUAGES.map((lng) => (
          <option key={lng} value={lng} lang={lng}>
            {LANGUAGE_NAMES[lng]}
          </option>
        ))}
      </select>
    </label>
  );
}
