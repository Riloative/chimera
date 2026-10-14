/**
 * Map stable backend error codes to localized text. English strings sent by
 * the server are only a fallback for codes the catalog doesn't know.
 */
import i18n from "@/i18n";
import { ApiError } from "./api";

type Params = Record<string, unknown> | undefined;

function translate(key: string, params: Params, fallback: string): string {
  return i18n.exists(key) ? String(i18n.t(key as never, (params ?? {}) as never)) : fallback;
}

/** Text for anything thrown by the API client (or any other error). */
export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "validation_error" && err.params?.field) {
      return translate("errors:validation_error_field", err.params, err.message);
    }
    return translate(`errors:${err.code}`, err.params, err.message || String(i18n.t("errors:unknown")));
  }
  return String(i18n.t("errors:unknown"));
}

/** Text for a provider failure stored on a message or connection. */
export function providerErrorText(code: string | null | undefined, params: Params, fallback: string | null | undefined): string {
  if (!code) return fallback ?? "";
  // Web errors (``web.*``) have their own section; the rest are provider failures.
  if (code.startsWith("web.")) return translate(`errors:${code}`, params, fallback ?? code);
  return translate(`errors:provider.${code}`, params, fallback ?? code);
}

/** Server-supplied detail (never translated), shown after the localized message. */
export function serverDetail(params: Params): string | null {
  const detail = params?.detail;
  return typeof detail === "string" && detail ? detail : null;
}
