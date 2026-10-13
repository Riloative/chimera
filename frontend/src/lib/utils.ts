import { clsx, type ClassValue } from "clsx";
import { currentLanguage } from "@/i18n";

export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}

export const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = isMac ? "⌘" : "Ctrl";

export function formatBytes(bytes: number, locale: string = currentLanguage()): string {
  const units = ["byte", "kilobyte", "megabyte", "gigabyte"] as const;
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return new Intl.NumberFormat(locale, {
    style: "unit",
    unit: units[i],
    unitDisplay: "short",
    maximumFractionDigits: i === 0 || value >= 10 ? 0 : 1,
  }).format(value);
}

export function formatNumber(n: number, locale: string = currentLanguage()): string {
  return new Intl.NumberFormat(locale).format(n);
}

export function compactNumber(n: number, locale: string = currentLanguage()): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function formatList(items: string[], locale: string = currentLanguage()): string {
  return new Intl.ListFormat(locale, { type: "conjunction" }).format(items);
}

export function modelLabel(model: string | null | undefined): string {
  if (!model) return "";
  const base = model.split(/[\\/]/).pop() || model;
  return base.replace(/\.(gguf|bin)$/i, "");
}

export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path (e.g. plain-http LAN access) */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export function downloadUrl(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
