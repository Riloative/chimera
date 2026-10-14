/**
 * Link detection for the composer. Must behave exactly like
 * `find_urls` in backend/chimera/web/urls.py (the server reads the same
 * links the composer shows); both are tested against
 * backend/tests/fixtures/url_detection.json.
 */

const URL_RE = /(?<![0-9A-Za-z/@])https?:\/\/[^\s<>"'` 　]+/gi;
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/;
const INLINE_CODE_RE = /(`+)[^`]*?\1/g;
const TRAILING = ".,;:!?'\"*_~";
const PAIRS: Record<string, string> = { ")": "(", "]": "[", "}": "{" };

export function stripCode(text: string): string {
  const out: string[] = [];
  let fence: string | null = null;
  for (const line of text.split("\n")) {
    const m = FENCE_RE.exec(line);
    if (fence === null) {
      if (m) {
        fence = m[1];
        continue;
      }
      out.push(line);
    } else if (m && m[1][0] === fence[0] && m[1].length >= fence.length && !line.trim().slice(m[1].length).trim()) {
      fence = null;
    }
  }
  return out.join("\n").replace(INLINE_CODE_RE, " ");
}

const count = (s: string, ch: string) => s.split(ch).length - 1;

export function trimUrl(url: string): string {
  while (url) {
    const last = url[url.length - 1];
    if (TRAILING.includes(last)) url = url.slice(0, -1);
    else if (last in PAIRS && count(url, PAIRS[last]) < count(url, last)) url = url.slice(0, -1);
    else break;
  }
  return url;
}

function hasHost(url: string): boolean {
  const rest = url.includes("://") ? url.split("://").slice(1).join("://") : "";
  const host = rest.split(/[/?#]/, 1)[0];
  return /[\p{L}\p{N}_\]]/u.test(host);
}

export function dedupeKey(url: string): string {
  const idx = url.indexOf("://");
  const scheme = url.slice(0, idx);
  const rest = url.slice(idx + 3).split("#")[0];
  const m = /^([^/?#]*)([\s\S]*)$/.exec(rest);
  const host = m ? m[1] : rest;
  let tail = m ? m[2] : "";
  if (tail === "/") tail = "";
  return `${scheme.toLowerCase()}://${host.toLowerCase()}${tail}`;
}

export function findUrls(text: string, limit = 5): { urls: string[]; total: number } {
  const found: string[] = [];
  const seen = new Set<string>();
  for (const match of stripCode(text || "").matchAll(URL_RE)) {
    const url = trimUrl(match[0]).split("#")[0];
    if (!hasHost(url)) continue;
    const key = dedupeKey(url);
    if (seen.has(key)) continue;
    seen.add(key);
    found.push(url);
  }
  return { urls: found.slice(0, Math.max(0, limit)), total: found.length };
}

/** Host name without "www." for compact display (never fetched). */
export function displayDomain(url: string): string {
  try {
    const host = new URL(url).hostname.replace(/\.$/, "");
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return url;
  }
}

/** "example.com/path…" shortened for chips. */
export function shortUrl(url: string, max = 48): string {
  const plain = url.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/$/, "");
  return plain.length > max ? `${plain.slice(0, max - 1)}…` : plain;
}
