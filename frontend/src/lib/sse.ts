/** Incremental Server-Sent Events parser for fetch() response bodies. */

export interface SSEMessage {
  event: string;
  data: string;
}

export class SSEParser {
  private buffer = "";
  private data: string[] = [];
  private event = "";
  private pendingCR = false;

  feed(text: string): SSEMessage[] {
    const out: SSEMessage[] = [];
    let input = text;
    if (this.pendingCR) {
      this.pendingCR = false;
      if (input.startsWith("\n")) input = input.slice(1);
    }
    this.buffer += input;
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      const line = this.buffer.slice(start, i);
      if (ch === "\r") {
        if (i + 1 < this.buffer.length) {
          if (this.buffer[i + 1] === "\n") i++;
        } else {
          this.pendingCR = true;
        }
      }
      start = i + 1;
      const msg = this.line(line);
      if (msg) out.push(msg);
    }
    this.buffer = this.buffer.slice(start);
    return out;
  }

  flush(): SSEMessage[] {
    const out: SSEMessage[] = [];
    if (this.buffer) {
      const msg = this.line(this.buffer);
      this.buffer = "";
      if (msg) out.push(msg);
    }
    const last = this.line("");
    if (last) out.push(last);
    return out;
  }

  private line(line: string): SSEMessage | null {
    if (line === "") {
      if (!this.data.length) {
        this.event = "";
        return null;
      }
      const msg = { event: this.event || "message", data: this.data.join("\n") };
      this.data = [];
      this.event = "";
      return msg;
    }
    if (line.startsWith(":")) return null;
    const idx = line.indexOf(":");
    const field = idx === -1 ? line : line.slice(0, idx);
    let value = idx === -1 ? "" : line.slice(idx + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    return null;
  }
}

export async function* readSSE(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SSEMessage> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      for (const msg of parser.feed(decoder.decode(value, { stream: true }))) yield msg;
    }
    for (const msg of parser.feed(decoder.decode())) yield msg;
    for (const msg of parser.flush()) yield msg;
  } finally {
    reader.releaseLock();
  }
}
