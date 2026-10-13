import { currentLanguage } from "@/i18n";
import type { Conversation } from "./types";

export type GroupKey = "pinned" | "today" | "yesterday" | "week" | "month" | "older";

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function groupConversations(items: Conversation[], now = new Date()): { key: GroupKey; items: Conversation[] }[] {
  const today = startOfDay(now);
  const day = 86_400_000;
  const groups: Record<GroupKey, Conversation[]> = { pinned: [], today: [], yesterday: [], week: [], month: [], older: [] };
  for (const c of items) {
    if (c.pinned) {
      groups.pinned.push(c);
      continue;
    }
    const t = startOfDay(new Date(c.updated_at));
    if (t >= today) groups.today.push(c);
    else if (t >= today - day) groups.yesterday.push(c);
    else if (t >= today - 7 * day) groups.week.push(c);
    else if (t >= today - 30 * day) groups.month.push(c);
    else groups.older.push(c);
  }
  return (Object.keys(groups) as GroupKey[])
    .map((key) => ({ key, items: groups[key].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) }))
    .filter((g) => g.items.length > 0);
}

/** Message timestamp: time today, otherwise date + time, in the UI language. */
export function formatTime(iso: string, locale: string = currentLanguage(), now = new Date()): string {
  const d = new Date(iso);
  const time = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(d);
  if (d.toDateString() === now.toDateString()) return time;
  const sameYear = d.getFullYear() === now.getFullYear();
  const date = new Intl.DateTimeFormat(locale, { month: "long", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) }).format(d);
  return `${date}, ${time}`;
}

export function formatDateTime(iso: string, locale: string = currentLanguage()): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

/** "just now", "5 minutes ago", "yesterday"… via Intl.RelativeTimeFormat. */
export function relativeTime(iso: string, locale: string = currentLanguage(), now = Date.now()): string {
  const diff = (new Date(iso).getTime() - now) / 1000;
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 45) return rtf.format(0, "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(iso));
}

export type GreetingKey = "night" | "morning" | "afternoon" | "evening";
export function greetingKey(now = new Date()): GreetingKey {
  const h = now.getHours();
  if (h < 5) return "night";
  if (h < 12) return "morning";
  if (h < 18) return "afternoon";
  return "evening";
}
