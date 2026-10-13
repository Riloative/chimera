import enAccount from "./locales/en/account.json";
import enAdmin from "./locales/en/admin.json";
import enAuth from "./locales/en/auth.json";
import enChat from "./locales/en/chat.json";
import enCommon from "./locales/en/common.json";
import enErrors from "./locales/en/errors.json";
import enKnowledge from "./locales/en/knowledge.json";
import enModels from "./locales/en/models.json";
import enPrompts from "./locales/en/prompts.json";
import enSettings from "./locales/en/settings.json";
import enSidebar from "./locales/en/sidebar.json";
import enWeb from "./locales/en/web.json";
import ltAccount from "./locales/lt/account.json";
import ltAdmin from "./locales/lt/admin.json";
import ltAuth from "./locales/lt/auth.json";
import ltChat from "./locales/lt/chat.json";
import ltCommon from "./locales/lt/common.json";
import ltErrors from "./locales/lt/errors.json";
import ltKnowledge from "./locales/lt/knowledge.json";
import ltModels from "./locales/lt/models.json";
import ltPrompts from "./locales/lt/prompts.json";
import ltSettings from "./locales/lt/settings.json";
import ltSidebar from "./locales/lt/sidebar.json";
import ltWeb from "./locales/lt/web.json";

export const LANGUAGES = ["en", "lt"] as const;
export type Language = (typeof LANGUAGES)[number];

/** Native names: shown as-is in every UI language. */
export const LANGUAGE_NAMES: Record<Language, string> = { en: "English", lt: "Lietuvių" };

const en = {
  common: enCommon,
  auth: enAuth,
  chat: enChat,
  sidebar: enSidebar,
  models: enModels,
  knowledge: enKnowledge,
  prompts: enPrompts,
  settings: enSettings,
  account: enAccount,
  admin: enAdmin,
  web: enWeb,
  errors: enErrors,
};

export const resources = {
  en,
  lt: {
    common: ltCommon,
    auth: ltAuth,
    chat: ltChat,
    sidebar: ltSidebar,
    models: ltModels,
    knowledge: ltKnowledge,
    prompts: ltPrompts,
    settings: ltSettings,
    account: ltAccount,
    admin: ltAdmin,
    web: ltWeb,
    errors: ltErrors,
  },
} as const;

export type Resources = typeof en;
