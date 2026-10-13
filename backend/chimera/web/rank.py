"""Relevance and context budgeting for web material (no embedding model needed).

Pages are split into passage-sized chunks along Markdown structure (code
blocks stay intact). Chunks are scored against the search queries and the
user's question with Okapi BM25, and a long page is reduced to its best
passages, kept in document order, instead of being cut off at an arbitrary
character position.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

CHUNK_CHARS = 900
MIN_SOURCE_CHARS = 400
GAP = "\n\n[…]\n\n"
CHARS_PER_TOKEN = 3.5  # conservative across English/Lithuanian Markdown

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = set(
    "a an and are as at be but by for from has have how i if in into is it its of on or that the their this to was "
    "were what when where which who why will with you your about can do does did not no yes".split()
)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def tokens_to_chars(tokens: int) -> int:
    return max(0, int(tokens * CHARS_PER_TOKEN))


def terms(text: str) -> list[str]:
    out = []
    for word in _WORD_RE.findall(text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        # Crude suffix folding helps both English and Lithuanian inflections.
        out.append(word[:7] if len(word) > 7 else word)
    return out


def chunk_markdown(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split on blank lines, keep fenced code blocks whole (unless huge), and
    pack consecutive blocks into chunks of about ``size`` characters."""
    blocks: list[str] = []
    buf: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            continue
        buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    # Split oversize blocks by lines, then by characters.
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= size * 2:
            pieces.append(block)
            continue
        cur = ""
        for line in block.split("\n"):
            while len(line) > size * 2:
                pieces.append(line[: size * 2])
                line = line[size * 2 :]
            if len(cur) + len(line) + 1 > size and cur:
                pieces.append(cur)
                cur = ""
            cur = f"{cur}\n{line}" if cur else line
        if cur:
            pieces.append(cur)
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + len(piece) + 2 > size:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def bm25_scores(chunks: list[str], query: str, k1: float = 1.5, b: float = 0.75) -> list[float]:
    q_terms = list(dict.fromkeys(terms(query)))
    if not chunks or not q_terms:
        return [0.0] * len(chunks)
    docs = [Counter(terms(c)) for c in chunks]
    lengths = [sum(d.values()) or 1 for d in docs]
    avg = sum(lengths) / len(lengths)
    n = len(docs)
    scores = []
    for doc, length in zip(docs, lengths):
        score = 0.0
        for term in q_terms:
            tf = doc.get(term, 0)
            if not tf:
                continue
            df = sum(1 for d in docs if term in d)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        scores.append(score)
    return scores


def fit_text(text: str, budget: int, query: str, *, focused: bool) -> tuple[str, bool]:
    """Reduce ``text`` to at most ``budget`` characters.

    *Full* mode keeps the whole text when it fits; otherwise (and always in
    *focused* mode) it keeps the highest-scoring chunks in document order.
    Full mode also always keeps the opening chunk (it usually says what the
    page is about). Returns ``(text, trimmed)``.
    """
    if budget <= 0:
        return "", bool(text)
    if len(text) <= budget and not focused:
        return text, False
    # Smaller passages for small budgets, so several relevant ones can fit.
    chunks = chunk_markdown(text, size=min(CHUNK_CHARS, max(200, budget // 4)))
    if len(chunks) <= 1:
        return (text, False) if len(text) <= budget else (text[:budget].rsplit(" ", 1)[0], True)
    scores = bm25_scores(chunks, query)
    order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    if not focused:
        order.remove(0)
        order.insert(0, 0)
    elif not any(scores):
        order = list(range(len(chunks)))  # nothing matched: read from the top
    chosen: set[int] = set()
    used = 0
    for i in order:
        cost = len(chunks[i]) + len(GAP)
        if used + cost > budget:
            if not chosen and focused:
                chosen.add(i)
                used = budget
            continue
        chosen.add(i)
        used += cost
    if not chosen:
        return text[:budget].rsplit(" ", 1)[0], True
    parts: list[str] = []
    last = -2
    for i in sorted(chosen):
        if parts and i != last + 1:
            parts.append("[…]")
        parts.append(chunks[i][:budget] if len(chunks[i]) > budget else chunks[i])
        last = i
    trimmed = len(chosen) < len(chunks)
    return "\n\n".join(parts), trimmed


@dataclass
class BudgetItem:
    key: str
    text: str  # extracted content ("" for failed sources)
    ok: bool
    focused: bool
    query: str
    # outputs
    fitted: str = ""
    trimmed: bool = False
    included: bool = False
    extra: dict = field(default_factory=dict)


FAILED_COST = 220  # a failure notice still has to be told to the model


def allocate(items: list[BudgetItem], total_chars: int, per_source_chars: int) -> list[BudgetItem]:
    """Share ``total_chars`` across sources in priority order (``items`` is
    already ordered: pasted links first, then search results by rank). Failed
    sources are always included (the model must be told the truth); sources
    that would get less than :data:`MIN_SOURCE_CHARS` are dropped."""
    remaining = total_chars
    for item in items:
        if not item.ok:
            item.included = True
            remaining -= FAILED_COST
    ok_items = [i for i in items if i.ok]
    if not ok_items:
        return items
    share = max(MIN_SOURCE_CHARS, remaining // len(ok_items)) if remaining > 0 else 0
    for item in ok_items:
        allowance = min(per_source_chars, share, remaining)
        if allowance < MIN_SOURCE_CHARS:
            item.included = False
            continue
        item.fitted, item.trimmed = fit_text(item.text, allowance, item.query, focused=item.focused)
        item.included = bool(item.fitted.strip())
        remaining -= len(item.fitted)
    # Hand unused budget to sources that were trimmed, best-ranked first.
    for item in ok_items:
        if remaining < MIN_SOURCE_CHARS:
            break
        if item.included and item.trimmed:
            allowance = min(per_source_chars, len(item.fitted) + remaining)
            refit, trimmed = fit_text(item.text, allowance, item.query, focused=item.focused)
            if len(refit) > len(item.fitted):
                remaining -= len(refit) - len(item.fitted)
                item.fitted, item.trimmed = refit, trimmed
    return items
