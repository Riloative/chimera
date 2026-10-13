/**
 * Localization: i18next + react-i18next with bundled JSON catalogs.
 * Nothing is fetched at runtime; every language ships in the bundle.
 * To add a language, see docs/I18N.md.
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { resources, LANGUAGES, type Language } from "./resources";

export { LANGUAGES, type Language };
export const STORAGE_KEY = "chimera.language";

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && (LANGUAGES as readonly string[]).includes(value);
}

/** Saved choice > browser preference > English. */
export function detectLanguage(): Language {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLanguage(saved)) return saved;
  } catch {
    /* storage unavailable */
  }
  const preferred = typeof navigator !== "undefined" ? navigator.languages ?? [navigator.language] : [];
  for (const tag of preferred) {
    const base = tag?.toLowerCase().split("-")[0];
    if (isLanguage(base)) return base;
  }
  return "en";
}

function applyDocumentLanguage(lng: string) {
  if (typeof document !== "undefined") document.documentElement.lang = lng;
}

i18n.use(initReactI18next).init({
  resources,
  lng: detectLanguage(),
  fallbackLng: "en",
  supportedLngs: [...LANGUAGES],
  defaultNS: "common",
  ns: Object.keys(resources.en),
  interpolation: { escapeValue: false }, // React already escapes
  returnNull: false,
  react: { useSuspense: false },
});
applyDocumentLanguage(i18n.language);
i18n.on("languageChanged", applyDocumentLanguage);

/** Switch language instantly; remember it locally (the account copy is saved by the caller). */
export function setLanguage(lng: Language) {
  try {
    localStorage.setItem(STORAGE_KEY, lng);
  } catch {
    /* ignore */
  }
  return i18n.changeLanguage(lng);
}

export function currentLanguage(): Language {
  return isLanguage(i18n.language) ? i18n.language : "en";
}

export default i18n;
