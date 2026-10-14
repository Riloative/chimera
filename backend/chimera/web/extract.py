"""Turn fetched bytes into clean, model-ready Markdown plus metadata.

HTML goes through `trafilatura <https://trafilatura.readthedocs.io>`_ (fully
local; strong main-content extraction; Markdown output that keeps headings,
lists, tables and code blocks). Metadata (title, description, canonical URL,
publication date) is read only from explicit ``<meta>``/JSON-LD declarations:
heuristic date guessing proved unreliable (it picks up copyright years).

Nothing fetched is ever rendered as HTML in the app; the output is plain
Markdown text that the web UI renders with its sanitizing renderer.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from ..services.documents import ExtractionError, extract_pdf
from .errors import WebError
from .fetcher import FetchResult

log = logging.getLogger("chimera.web")

MIN_TEXT_CHARS = 200
META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_\-:.]+)""", re.I)
XML_ENCODING_RE = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9_\-.]+)""", re.I)
# Browsers treat these labels as windows-1252 (WHATWG Encoding Standard).
CHARSET_ALIASES = {"iso-8859-1": "cp1252", "latin1": "cp1252", "latin-1": "cp1252", "us-ascii": "cp1252", "ascii": "cp1252",
                   "x-user-defined": "cp1252", "utf8": "utf-8", "win-1257": "cp1257", "windows-1257": "cp1257"}
JS_HINT_RE = re.compile(r"(enable|turn on|activate)\s+javascript|javascript\s+(is\s+)?(required|disabled)|requires javascript", re.I)


@dataclass
class Page:
    kind: str  # html | text | json | csv | feed | xml | pdf | video
    markdown: str
    title: str | None = None
    description: str | None = None
    site_name: str | None = None
    published: str | None = None
    canonical: str | None = None
    language: str | None = None
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- decoding
def _norm_charset(name: str | None) -> str | None:
    if not name:
        return None
    label = name.strip().strip("\"'").lower()
    label = CHARSET_ALIASES.get(label, label)
    try:
        import codecs

        return codecs.lookup(label).name
    except LookupError:
        return None


def detect_charset(body: bytes, header_charset: str | None, html: bool) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if body.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    declared = _norm_charset(header_charset)
    if declared:
        return declared
    head = body[:4096]
    m = (META_CHARSET_RE.search(head) if html else None) or XML_ENCODING_RE.search(head)
    if m:
        declared = _norm_charset(m.group(1).decode("ascii", "ignore"))
        if declared:
            return declared
    try:
        body.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(body[:200_000]).best()
        if best and best.encoding:
            return _norm_charset(best.encoding) or "cp1252"
    except Exception:  # noqa: BLE001
        pass
    return "cp1252"


def decode(body: bytes, header_charset: str | None, html: bool = False) -> str:
    if b"\x00" in body[:4096] and not body.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise WebError("web.binary_content", "The address returned binary data, not readable text.")
    charset = detect_charset(body, header_charset, html)
    return body.decode(charset, errors="replace").replace("\r\n", "\n").replace("\x00", "")


# --------------------------------------------------------------------- HTML
def _clean(value: object, limit: int = 500) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit] or None


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else None


def _json_ld_date(doc) -> str | None:
    for node in doc.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(node.text_content() or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                for key in ("datePublished", "dateCreated", "uploadDate"):
                    if isinstance(item.get(key), str):
                        return item[key]
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return None


def _parse_html(html: str):
    """Parse already-decoded HTML (the page's own charset declaration must
    not make lxml decode it a second time)."""
    from lxml import html as lhtml

    parser = lhtml.HTMLParser(encoding="utf-8", remove_comments=True)
    return lhtml.document_fromstring(html.encode("utf-8", "replace"), parser=parser)


def html_metadata(html: str) -> dict[str, str | None]:
    try:
        doc = _parse_html(html)
    except Exception:  # noqa: BLE001
        return {}

    def meta(*names: str) -> str | None:
        for name in names:
            for attr in ("property", "name", "itemprop"):
                found = doc.xpath(f'//meta[translate(@{attr}, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")="{name}"]/@content')
                if found and str(found[0]).strip():
                    return str(found[0])
        return None

    titles = doc.xpath("//title/text()")
    canonical = doc.xpath('//link[translate(@rel, "CANONICAL", "canonical")="canonical"]/@href')
    published = meta("article:published_time", "og:published_time", "datepublished", "date", "pubdate", "publish-date",
                     "dc.date", "dc.date.issued", "dcterms.created", "sailthru.date") or _json_ld_date(doc)
    lang = doc.xpath("/html/@lang")
    return {
        "title": _clean(meta("og:title", "twitter:title") or (titles[0] if titles else None), 300),
        "description": _clean(meta("og:description", "description", "twitter:description"), 600),
        "site_name": _clean(meta("og:site_name", "application-name"), 120),
        "canonical": _clean(canonical[0], 2000) if canonical else None,
        "published": _iso_date(published),
        "language": _clean(lang[0], 20) if lang else None,
    }


def _fallback_text(html: str) -> tuple[str, int, bool]:
    """Visible text without boilerplate elements; also counts scripts."""
    try:
        doc = _parse_html(html)
    except Exception:  # noqa: BLE001
        return "", 0, False
    scripts = len(doc.xpath("//script"))
    noscript = " ".join(n.text_content() for n in doc.xpath("//noscript"))
    for bad in doc.xpath("//script|//style|//noscript|//template|//nav|//header|//footer|//aside|//form|//svg"):
        bad.drop_tree()
    body = doc.find("body")
    text = re.sub(r"[ \t]+", " ", (body if body is not None else doc).text_content())
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text, scripts, bool(JS_HINT_RE.search(noscript))


def extract_html(html: str, url: str) -> Page:
    import trafilatura

    meta = html_metadata(html)
    markdown = None
    try:
        markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_formatting=True,
            include_links=False,
            include_images=False,
            include_comments=True,
            favor_recall=True,
            deduplicate=False,
        )
    except Exception:  # noqa: BLE001 - extraction must never crash a turn
        log.info("trafilatura failed; using fallback text")
    fallback, scripts, js_hint = _fallback_text(html)
    text = (markdown or "").strip()
    if len(text) < MIN_TEXT_CHARS and len(fallback) > len(text):
        text = fallback
    if len(text) < MIN_TEXT_CHARS and (js_hint or (scripts >= 2 and len(text) < 80)):
        raise WebError("web.needs_javascript", "This page appears to require JavaScript to show its content.",
                       title=meta.get("title"))
    if not text and meta.get("description"):
        text = meta["description"] or ""
    if not text.strip():
        raise WebError("web.extraction_empty", "No readable text was found on this page.", title=meta.get("title"))
    return Page(kind="html", markdown=text, **meta)  # type: ignore[arg-type]


# ------------------------------------------------------------------ others
def _secure_xml(data: bytes):
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, recover=True)
    return etree.fromstring(data, parser=parser)


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def extract_xml(body: bytes) -> Page:
    try:
        root = _secure_xml(body.lstrip())
    except Exception as exc:  # noqa: BLE001
        raise WebError("web.extraction_empty", "No readable text was found on this page.") from exc
    if root is None:
        raise WebError("web.extraction_empty", "No readable text was found on this page.")
    name = _local(root.tag)
    if name in ("rss", "feed", "rdf"):
        title = None
        items = []
        for el in root.iter():
            tag = _local(el.tag)
            if tag == "title" and title is None and _local(el.getparent().tag) in ("channel", "feed"):
                title = _clean(el.text, 300)
            if tag in ("item", "entry"):
                fields: dict[str, str | None] = {}
                for child in el:
                    ctag = _local(child.tag)
                    if ctag == "link":
                        fields.setdefault("link", child.get("href") or _clean(child.text, 2000))
                    elif ctag in ("title", "pubdate", "published", "updated", "date", "description", "summary"):
                        fields.setdefault(ctag, _clean(re.sub(r"<[^>]+>", " ", child.text or ""), 600))
                items.append(fields)
        lines = []
        for item in items[:100]:
            date = item.get("pubdate") or item.get("published") or item.get("updated") or item.get("date")
            line = f"- **{item.get('title') or '(untitled)'}**"
            if date:
                line += f" ({date})"
            if item.get("link"):
                line += f" — {item['link']}"
            summary = item.get("description") or item.get("summary")
            if summary:
                line += f"\n  {summary}"
            lines.append(line)
        if not lines:
            raise WebError("web.extraction_empty", "No readable text was found on this page.")
        return Page(kind="feed", markdown="\n".join(lines), title=title)
    text = re.sub(r"\s+\n", "\n", "\n".join(t.strip() for t in root.itertext() if t and t.strip()))
    if not text:
        raise WebError("web.extraction_empty", "No readable text was found on this page.")
    return Page(kind="xml", markdown=text)


def extract_json(text: str) -> Page:
    try:
        pretty = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return Page(kind="text", markdown=text)
    return Page(kind="json", markdown=f"```json\n{pretty}\n```")


def extract_csv(text: str) -> Page:
    try:
        rows = list(csv.reader(io.StringIO(text[:200_000])))
    except csv.Error:
        rows = []
    if rows and len(rows) <= 200 and all(len(r) == len(rows[0]) for r in rows) and 1 < len(rows[0]) <= 20:
        esc = [[c.replace("|", "\\|").replace("\n", " ") for c in r] for r in rows]
        lines = ["| " + " | ".join(esc[0]) + " |", "|" + "---|" * len(esc[0])]
        lines += ["| " + " | ".join(r) + " |" for r in esc[1:]]
        return Page(kind="csv", markdown="\n".join(lines))
    return Page(kind="csv", markdown=f"```csv\n{text}\n```")


# -------------------------------------------------------------------- entry
def extract_page(result: FetchResult, max_chars: int, kind_hint: str | None = None) -> Page:
    ctype = result.content_type
    body = result.body
    if not body.strip():
        raise WebError("web.extraction_empty", "No readable text was found on this page.")
    if ctype == "application/pdf":
        try:
            page = Page(kind="pdf", markdown=extract_pdf(body))
        except ExtractionError as exc:
            code = {"pdf_encrypted": "web.pdf_encrypted", "pdf_no_text": "web.pdf_no_text"}.get(exc.code, "web.pdf_unreadable")
            raise WebError(code, str(exc)) from exc
    elif ctype in ("text/html", "application/xhtml+xml"):
        html = decode(body, result.charset, html=True)
        if kind_hint == "video":
            meta = html_metadata(html)
            parts = [p for p in (meta.get("title"), meta.get("description")) if p]
            if not parts:
                raise WebError("web.extraction_empty", "No readable text was found on this page.")
            page = Page(kind="video", markdown="\n\n".join(parts), notes=["transcript_unavailable"],
                        **{k: v for k, v in meta.items()})  # type: ignore[arg-type]
        else:
            page = extract_html(html, result.final_url)
    elif ctype in ("application/xml", "text/xml") or ctype.endswith("+xml"):
        page = extract_xml(body)
    elif ctype in ("application/json", "application/ld+json") or ctype.endswith("+json"):
        page = extract_json(decode(body, result.charset))
    elif ctype == "text/csv":
        page = extract_csv(decode(body, result.charset))
    else:
        text = decode(body, result.charset)
        page = Page(kind="text", markdown=text)
    page.markdown = page.markdown.strip()
    if not page.markdown:
        raise WebError("web.extraction_empty", "No readable text was found on this page.")
    if len(page.markdown) > max_chars:
        page.markdown = page.markdown[:max_chars].rsplit("\n", 1)[0] or page.markdown[:max_chars]
        page.truncated = True
    return page
