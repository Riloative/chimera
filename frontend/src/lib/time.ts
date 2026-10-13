import type { Conversation } from "./types";

export type GroupKey = "Pinned" | "Today" | "Yesterday" | "Previous 7 Days" | "Previous 30 Days" | "Older";

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function groupConversations(items: Conversation[], now = new Date()): { key: GroupKey; items: Conversation[] }[] {
  const today = startOfDay(now);
  const day = 86_400_000;
  const groups: Record<GroupKey, Conversation[]> = {
    Pinned: [],
    Today: [],
    Yesterday: [],
    "Previous 7 Days": [],
    "Previous 30 Days": [],
    Older: [],
  };
  for (const c of items) {
    if (c.pinned) {
      groups.Pinned.push(c);
      continue;
    }
    const t = startOfDay(new Date(c.updated_at));
    if (t >= today) groups.Today.push(c);
    else if (t >= today - day) groups.Yesterday.push(c);
    else if (t >= today - 7 * day) groups["Previous 7 Days"].push(c);
    else if (t >= today - 30 * day) groups["Previous 30 Days"].push(c);
    else groups.Older.push(c);
  }
  return (Object.keys(groups) as GroupKey[])
    .map((key) => ({ key, items: groups[key].sort((a, b) => b.updated_at.localeCompare(a.updated_at)) }))
    .filter((g) => g.items.length > 0);
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (sameDay) return time;
  const sameYear = d.getFullYear() === now.getFullYear();
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) })}, ${time}`;
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export function greeting(now = new Date()): string {
  const h = now.getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}
