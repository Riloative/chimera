import type { Conversation } from "@/lib/types";
import { useChat } from "./chat";
import { useConversations } from "./conversations";

const CONV: Conversation = {
  id: "c1",
  title: "New conversation",
  title_source: "default",
  provider_id: "p",
  model: "m",
  system_prompt: null,
  params: {},
  collection_ids: [],
  pinned: false,
  created_at: "2026-11-18T09:00:00Z",
  updated_at: "2026-11-18T09:00:00Z",
} as Conversation;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

/** A fetch() Response whose body streams the given SSE events. */
function sse(events: [string, unknown][]) {
  const text = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const assistant = { id: "a1", conversation_id: "c1", parent_id: "u1", position: 1, role: "assistant", content: "", status: "streaming", created_at: "2026-11-18T09:00:01Z" };

beforeEach(() => {
  useConversations.setState({ items: [{ ...CONV }], loaded: true, error: null });
  useChat.setState({ convs: {} });
});
afterEach(() => vi.unstubAllGlobals());

const title = () => useConversations.getState().items.find((c) => c.id === "c1")?.title;

describe("live conversation titles", () => {
  it("shows the fallback title from the start event, then the model's title, without a reload", async () => {
    const seen: string[] = [];
    const unsubscribe = useConversations.subscribe((s) => {
      const t = s.items.find((c) => c.id === "c1")?.title;
      if (t && seen.at(-1) !== t) seen.push(t);
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/messages")) {
          return sse([
            ["start", { generation_id: "g1", conversation_id: "c1", assistant_message: assistant, active_path: ["u1", "a1"], conversation: { id: "c1", title: "Testas-testas?", title_source: "fallback" } }],
            ["delta", { content: "Hi" }],
            ["done", { message: { ...assistant, content: "Hi", status: "complete" } }],
            ["title", { conversation_id: "c1", title: "Lithuanian greeting test" }],
            ["end", {}],
          ]);
        }
        return json({ providers: [] });
      }),
    );
    await useChat.getState().send("c1", { content: "Testas-testas?" } as never);
    unsubscribe();
    expect(seen).toEqual(["Testas-testas?", "Lithuanian greeting test"]);
    expect(useConversations.getState().items[0].title_source).toBe("model");
  });

  it("keeps a live title when an older conversation list arrives later", async () => {
    let resolveList: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (resolveList = r))));
    const loading = useConversations.getState().load();
    // While the list request is in flight: a title arrives and a new chat is created.
    useConversations.getState().upsert({ id: "c1", title: "Fresh title", title_source: "model" });
    useConversations.getState().upsert({ ...CONV, id: "c2", title: "Brand new chat" });
    resolveList(json([{ ...CONV, title: "New conversation" }]));
    await loading;
    const items = useConversations.getState().items;
    expect(items.find((c) => c.id === "c1")?.title).toBe("Fresh title");
    expect(items.find((c) => c.id === "c2")?.title).toBe("Brand new chat");
  });

  it("never overwrites a title the user set", async () => {
    useConversations.setState({ items: [{ ...CONV, title: "Mine", title_source: "user" }] });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sse([
          ["start", { generation_id: "g1", conversation_id: "c1", assistant_message: assistant, active_path: ["u1", "a1"], conversation: { id: "c1", title: "Mine", title_source: "user" } }],
          ["done", { message: { ...assistant, status: "complete" } }],
          ["end", {}],
        ]),
      ),
    );
    await useChat.getState().send("c1", { content: "hello" } as never);
    expect(title()).toBe("Mine");
  });
});
