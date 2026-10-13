"""Safe local file ingestion: validation and text extraction.

Uploaded files are never executed or served back with an active content type.
Text is extracted in-process; originals are kept only for images (which are
sent to vision-capable models as base64).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import PurePath

log = logging.getLogger("chimera.documents")

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".xml", ".html", ".htm", ".css", ".scss", ".sql", ".tex",
    ".py", ".pyi", ".ipynb", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".java", ".kt", ".kts", ".scala", ".go", ".rs",
    ".rb", ".php", ".swift", ".m", ".mm", ".lua", ".r", ".jl", ".pl", ".sh", ".bash", ".zsh",
    ".fish", ".ps1", ".bat", ".dart", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj", ".zig",
    ".nim", ".proto", ".graphql", ".gql", ".dockerfile", ".gradle", ".cmake", ".mk", ".diff", ".patch",
    ".srt", ".vtt", ".bib",
}
TEXT_FILENAMES = {"dockerfile", "makefile", "readme", "license", "changelog", "gemfile", "procfile"}
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ExtractionError(Exception):
    pass


@dataclass
class Extracted:
    kind: str  # text | image
    mime: str
    text: str | None = None
    truncated: bool = False


def safe_filename(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.strip() or "file"
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    return cleaned[:200] or "file"


def classify(filename: str) -> str | None:
    path = PurePath(filename.lower())
    if path.suffix == ".pdf":
        return "pdf"
    if path.suffix in IMAGE_TYPES:
        return "image"
    if path.suffix in TEXT_EXTENSIONS or path.name in TEXT_FILENAMES or path.stem in TEXT_FILENAMES and not path.suffix:
        return "text"
    return None


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_text(data: bytes) -> str:
    if b"\x00" in data[:8192] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ExtractionError("This looks like a binary file, not text.")
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            if encoding == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text[:4096])
        if printable / max(1, min(len(text), 4096)) < 0.9:
            raise ExtractionError("Could not decode this file as text.") from None
        return text


def extract_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ExtractionError("The file does not look like a valid PDF.")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise ExtractionError("The PDF is password protected.") from exc
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Page {number}]\n{text}")
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.info("pdf extraction failed: %s", exc)
        raise ExtractionError("Could not read text from this PDF.") from exc
    if not pages:
        raise ExtractionError("No extractable text found (the PDF may be scanned images).")
    return "\n\n".join(pages)


def extract(filename: str, data: bytes, max_bytes: int, max_chars: int) -> Extracted:
    if not data:
        raise ExtractionError("The file is empty.")
    kind = classify(filename)
    if kind is None:
        raise ExtractionError("Unsupported file type.")
    if kind == "image":
        if len(data) > min(max_bytes, MAX_IMAGE_BYTES):
            raise ExtractionError("Image is too large.")
        mime = _sniff_image(data)
        if not mime:
            raise ExtractionError("The file content is not a supported image.")
        return Extracted(kind="image", mime=mime)
    if len(data) > max_bytes:
        raise ExtractionError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    if kind == "pdf":
        text, mime = extract_pdf(data), "application/pdf"
    else:
        text, mime = decode_text(data), "text/plain"
    text = text.replace("\r\n", "\n").replace("\x00", "")
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    if not text.strip():
        raise ExtractionError("The file contains no text.")
    return Extracted(kind="text", mime=mime, text=text, truncated=truncated)
