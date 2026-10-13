"""Incremental Server-Sent Events parser.

Handles arbitrary chunk boundaries (including mid-line and mid-UTF-8
sequence), CRLF/CR/LF line endings, comments, multi-line ``data`` fields and
the ``event`` field, following the WHATWG EventSource processing model.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import AsyncIterator, Iterator


@dataclass
class SSEEvent:
    event: str
    data: str


class SSEParser:
    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""
        self._data: list[str] = []
        self._event = ""

    def feed(self, chunk: bytes | str) -> Iterator[SSEEvent]:
        text = self._decoder.decode(chunk) if isinstance(chunk, bytes) else chunk
        self._buffer += text
        while True:
            idx = _find_line_end(self._buffer)
            if idx is None:
                break
            end, skip = idx
            line = self._buffer[:end]
            self._buffer = self._buffer[end + skip :]
            event = self._process_line(line)
            if event is not None:
                yield event

    def flush(self) -> Iterator[SSEEvent]:
        """Dispatch whatever is left at end of stream (lenient)."""
        tail = self._decoder.decode(b"", final=True)
        self._buffer += tail
        if self._buffer:
            line, self._buffer = self._buffer.rstrip("\r"), ""
            event = self._process_line(line)
            if event is not None:
                yield event
        event = self._process_line("")
        if event is not None:
            yield event

    def _process_line(self, line: str) -> SSEEvent | None:
        if line == "":
            if not self._data:
                self._event = ""
                return None
            event = SSEEvent(event=self._event or "message", data="\n".join(self._data))
            self._data = []
            self._event = ""
            return event
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        # "id" and "retry" are irrelevant for one-shot completions.
        return None


def _find_line_end(buffer: str) -> tuple[int, int] | None:
    for i, ch in enumerate(buffer):
        if ch == "\n":
            return i, 1
        if ch == "\r":
            if i + 1 < len(buffer):
                return (i, 2) if buffer[i + 1] == "\n" else (i, 1)
            return None  # wait: could be the first half of CRLF
    return None


async def iter_sse(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[SSEEvent]:
    parser = SSEParser()
    async for chunk in byte_stream:
        for event in parser.feed(chunk):
            yield event
    for event in parser.flush():
        yield event
