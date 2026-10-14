"""URL detection in messages, normalization and site-specific rewriters.

``find_urls`` must behave exactly like ``findUrls`` in
``frontend/src/lib/urls.ts`` (the composer shows the same links the server
will read); both are tested against ``tests/fixtures/url_detection.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"(?<![0-9A-Za-z/@])https?://[^\s<>\"'`\u00a0\u3000]+", re.IGNORECASE)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE_RE = re.compile(r"(`+)[^`]*?\1")
TRAILING = ".,;:!?'\"*_~"
PAIRS = {")": "(", "]": "[", "}": "{"}

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid",
    "_hsenc", "_hsmi", "mkt_tok", "ref_src", "ref_url", "s_cid", "srsltid", "oly_anon_id", "oly_enc_id",
    "vero_id", "__s", "rb_clickid", "twclid", "ttclid", "li_fat_id",
}


def strip_code(text: str) -> str:
    """Remove fenced code blocks and inline code spans (URLs there are not read)."""
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            out.append(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
            fence = None
    return INLINE_CODE_RE.sub(" ", "\n".join(out))


def trim_url(url: str) -> str:
    while url:
        last = url[-1]
        if last in TRAILING:
            url = url[:-1]
        elif last in PAIRS and url.count(PAIRS[last]) < url.count(last):
            url = url[:-1]
        else:
            break
    return url


def _has_host(url: str) -> bool:
    rest = url.split("://", 1)[1] if "://" in url else ""
    host = re.split(r"[/?#]", rest, maxsplit=1)[0]
    return bool(re.search(r"[\w\]]", host))


def dedupe_key(url: str) -> str:
    scheme, rest = url.split("://", 1)
    rest = rest.split("#", 1)[0]
    m = re.match(r"([^/?#]*)(.*)", rest, re.S)
    host, tail = (m.group(1), m.group(2)) if m else (rest, "")
    if tail == "/":
        tail = ""
    return f"{scheme.lower()}://{host.lower()}{tail}"


@dataclass
class Detected:
    urls: list[str]
    total: int  # distinct URLs found (may exceed the limit)


def find_urls(text: str, limit: int = 5) -> Detected:
    found: list[str] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(strip_code(text or "")):
        url = trim_url(match.group(0)).split("#", 1)[0]
        if not _has_host(url):
            continue
        key = dedupe_key(url)
        if key in seen:
            continue
        seen.add(key)
        found.append(url)
    return Detected(urls=found[: max(0, limit)], total=len(found))


# ---------------------------------------------------------------- normalizing
def _is_tracking(name: str) -> bool:
    lower = name.lower()
    return lower.startswith("utm_") or lower in TRACKING_PARAMS


def strip_tracking(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))


def canonical_url(url: str) -> str:
    """Key used to deduplicate search results that point at the same page."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return url.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    scheme = parts.scheme.lower()
    if port and port != (443 if scheme == "https" else 80):
        host = f"{host}:{port}"
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k))
    path = parts.path.rstrip("/") or ""
    scheme = "https" if scheme in ("http", "https") else scheme  # http/https variants are one page
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def display_domain(url: str) -> str:
    host = host_of(url)
    return host[4:] if host.startswith("www.") else host


def domain_matches(host: str, entries: list[str]) -> bool:
    """``example.com`` (or ``*.example.com``) matches the domain and its subdomains."""
    host = host.lower().rstrip(".")
    for raw in entries:
        entry = raw.strip().lower().rstrip(".").removeprefix("*.").removeprefix(".")
        if entry and (host == entry or host.endswith("." + entry)):
            return True
    return False


# ------------------------------------------------------------------ rewriters
@dataclass
class Rewrite:
    """How to fetch a URL: candidate URLs (tried in order) and a content hint."""

    candidates: list[str]
    kind: str | None = None  # e.g. "video"
    notes: list[str] = field(default_factory=list)


Rewriter = Callable[[str], "Rewrite | None"]


def _github(url: str) -> Rewrite | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) >= 5 and segs[2] in ("blob", "raw"):
        owner, repo, _, ref, *path = segs
        return Rewrite([f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{'/'.join(path)}", url])
    if len(segs) == 2 and not parts.query:
        owner, repo = segs
        return Rewrite([f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md", url])
    return None


def _youtube(url: str) -> Rewrite | None:
    host = (urlsplit(url).hostname or "").lower()
    if host in ("youtu.be", "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
        return Rewrite([url], kind="video", notes=["transcript_unavailable"])
    return None


# Order matters: the first rewriter that returns something wins.
REWRITERS: list[Rewriter] = [_github, _youtube]


def rewrite(url: str) -> Rewrite:
    for rewriter in REWRITERS:
        try:
            result = rewriter(url)
        except ValueError:
            result = None
        if result:
            return result
    return Rewrite([url])
