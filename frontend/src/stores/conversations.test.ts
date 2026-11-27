import { useConversations } from "./conversations";

describe("conversation changes in flight", () => {
  it("regression: a message waits until a level chosen a moment ago is saved", async () => {
    let release: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (release = r))));
    useConversations.setState({ items: [{ id: "c1", thinking: "default" } as never] });
    const saving = useConversations.getState().patch("c1", { thinking: "high" });
    let settled = false;
    const waiting = useConversations.getState().settled("c1").then(() => (settled = true));
    await Promise.resolve();
    expect(settled).toBe(false); // still saving: sending now would use the old level
    expect(useConversations.getState().items[0].thinking).toBe("high"); // shown at once
    release(new Response(JSON.stringify({ id: "c1", thinking: "high" }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await saving;
    await waiting;
    expect(settled).toBe(true);
    await useConversations.getState().settled("other"); // nothing pending: resolves at once
  });
});
