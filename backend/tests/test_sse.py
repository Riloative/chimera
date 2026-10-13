from chimera.providers.sse import SSEParser


def collect(chunks):
    parser = SSEParser()
    events = []
    for chunk in chunks:
        events.extend(parser.feed(chunk))
    events.extend(parser.flush())
    return [(e.event, e.data) for e in events]


def test_basic_events():
    assert collect([b"data: a\n\ndata: b\n\n"]) == [("message", "a"), ("message", "b")]


def test_arbitrary_chunk_boundaries():
    raw = b'data: {"x": 1}\n\nevent: error\ndata: {"y": 2}\n\n'
    for size in range(1, len(raw)):
        chunks = [raw[i : i + size] for i in range(0, len(raw), size)]
        assert collect(chunks) == [("message", '{"x": 1}'), ("error", '{"y": 2}')]


def test_crlf_and_cr_line_endings():
    assert collect([b"data: a\r\n\r\ndata: b\r\r"]) == [("message", "a"), ("message", "b")]
    # CRLF split across chunks must not create an extra empty line
    assert collect([b"data: a\r", b"\n\r\n"]) == [("message", "a")]


def test_comments_and_multiline_data():
    assert collect([b": keepalive\n\ndata: line1\ndata: line2\n\n"]) == [("message", "line1\nline2")]


def test_utf8_split_inside_codepoint():
    raw = "data: héllo 👋\n\n".encode()
    idx = raw.index("👋".encode()) + 2
    assert collect([raw[:idx], raw[idx:]]) == [("message", "héllo 👋")]


def test_unterminated_final_event_is_flushed():
    assert collect([b"data: tail"]) == [("message", "tail")]


def test_field_without_space():
    assert collect([b"data:x\n\n"]) == [("message", "x")]
