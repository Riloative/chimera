import { SSEParser, readSSE } from "./sse";

function parseAll(chunks: string[]) {
  const p = new SSEParser();
  const out = chunks.flatMap((c) => p.feed(c));
  return [...out, ...p.flush()];
}

describe("SSEParser", () => {
  it("parses events across arbitrary chunk boundaries", () => {
    const raw = 'event: start\ndata: {"a":1}\n\ndata: {"b":2}\n\n';
    for (let size = 1; size < raw.length; size++) {
      const chunks: string[] = [];
      for (let i = 0; i < raw.length; i += size) chunks.push(raw.slice(i, i + size));
      expect(parseAll(chunks)).toEqual([
        { event: "start", data: '{"a":1}' },
        { event: "message", data: '{"b":2}' },
      ]);
    }
  });

  it("handles CRLF split across chunks, comments and multi-line data", () => {
    expect(parseAll(["data: a\r", "\n\r\n: keepalive\n\ndata: x\ndata: y\n\n"])).toEqual([
      { event: "message", data: "a" },
      { event: "message", data: "x\ny" },
    ]);
  });

  it("reads a byte stream with split UTF-8 sequences", async () => {
    const bytes = new TextEncoder().encode('data: {"t":"héllo 👋"}\n\n');
    const cut = bytes.length - 6;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    });
    const events = [];
    for await (const e of readSSE(body)) events.push(e);
    expect(JSON.parse(events[0].data).t).toBe("héllo 👋");
  });
});
