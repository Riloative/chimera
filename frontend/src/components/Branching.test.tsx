import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { errorText, providerErrorText } from "@/lib/errors";
import type { ContextEstimate, Message } from "@/lib/types";
import { pathMessages, siblingsOf, subtreeIds, useChat } from "@/stores/chat";
import { useUI } from "@/stores/ui";
import { BranchNav } from "./BranchNav";
import { ContextIndicator, ContextMarker } from "./ContextIndicator";

function msg(id: string, parent: string | null, role: Message["role"], minute: number, extra: Partial<Message> = {}): Message {
  return {
    id,
    conversation_id: "c1",
    parent_id: parent,
    position: 0,
    role,
    content: id,
    reasoning: "",
    status: "complete",
    error: null,
    provider_id: null,
    model: null,
    attachments: [],
    sources: [],
    metrics: {},
    created_at: `2026-03-02T10:${String(minute).padStart(2, "0")}:00Z`,
    updated_at: "",
    ...extra,
  } as Message;
}

// u1 ─ a1 ─ u2 ─ a2
//    └ a1b      └ u2b ─ a2b   (u2b is an edit of u2, a1b a regenerated answer)
const TREE = [
  msg("u1", null, "user", 0),
  msg("a1", "u1", "assistant", 1),
  msg("u2", "a1", "user", 2),
  msg("a2", "u2", "assistant", 3),
  msg("a1b", "u1", "assistant", 4),
  msg("u2b", "a1", "user", 5),
  msg("a2b", "u2b", "assistant", 6),
];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await act(() => i18n.changeLanguage("en"));
});

describe("message tree helpers", () => {
  it("collects a whole subtree", () => {
    expect(subtreeIds(TREE, "a1").sort()).toEqual(["a1", "a2", "a2b", "u2", "u2b"]);
    expect(subtreeIds(TREE, "a2b")).toEqual(["a2b"]);
  });

  it("lists siblings oldest first, without unsaved placeholders", () => {
    const withTemp = [...TREE, msg("temp-1", "u1", "assistant", 9)];
    expect(siblingsOf(withTemp, TREE[1]).map((m) => m.id)).toEqual(["a1", "a1b"]);
    expect(siblingsOf(TREE, TREE[2]).map((m) => m.id)).toEqual(["u2", "u2b"]);
  });

  it("returns the messages on the active path in order", () => {
    expect(pathMessages({ messages: TREE, activePath: ["u1", "a1", "u2b", "a2b"] }).map((m) => m.id)).toEqual(["u1", "a1", "u2b", "a2b"]);
    expect(pathMessages(undefined)).toEqual([]);
  });

  it("switching a branch stores the path the server returns", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ active_leaf_id: "a2", active_path: ["u1", "a1", "u2", "a2"] }));
    vi.stubGlobal("fetch", fetchMock);
    useChat.setState({ convs: { c1: { ...useChat.getState().convs.c1, messages: TREE, activePath: ["u1", "a1", "u2b", "a2b"], loaded: true } as never } });
    await useChat.getState().switchBranch("c1", "u2");
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations/c1/active", expect.objectContaining({ method: "POST", body: JSON.stringify({ message_id: "u2" }) }));
    expect(useChat.getState().convs.c1.activePath).toEqual(["u1", "a1", "u2", "a2"]);
  });
});

describe("BranchNav", () => {
  it("is hidden when there is only one version", () => {
    const { container } = render(<BranchNav index={0} total={1} kind="user" onSelect={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is a labelled group that works with the keyboard", async () => {
    const onSelect = vi.fn();
    render(<BranchNav index={1} total={3} kind="assistant" onSelect={onSelect} />);
    const group = screen.getByRole("group", { name: "Response 2 of 3" });
    expect(within(group).getByText("2 / 3")).toBeInTheDocument();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Previous response" })).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenLastCalledWith(0);
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "Next response" })).toHaveFocus();
    await userEvent.keyboard(" ");
    expect(onSelect).toHaveBeenLastCalledWith(2);
  });

  it("disables the arrows at either end", () => {
    const { rerender } = render(<BranchNav index={0} total={2} kind="user" onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Previous version" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next version" })).toBeEnabled();
    rerender(<BranchNav index={1} total={2} kind="user" onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Next version" })).toBeDisabled();
  });

  it("is translated", async () => {
    await i18n.changeLanguage("lt");
    render(<BranchNav index={0} total={2} kind="user" onSelect={() => {}} />);
    expect(screen.getByRole("group", { name: "1 versija iš 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kita versija" })).toBeInTheDocument();
  });
});

describe("ContextMarker", () => {
  it("says how many messages were left out and offers to change the strategy", async () => {
    const onChange = vi.fn();
    render(<ContextMarker dropped={4} summary={null} onChangeStrategy={onChange} />);
    expect(screen.getByRole("note")).toHaveTextContent("4 earlier messages are not included in the model's context.");
    await userEvent.click(screen.getByRole("button", { name: "Change strategy" }));
    expect(onChange).toHaveBeenCalled();
  });

  it("shows the summary read-only on request", async () => {
    render(<ContextMarker dropped={1} summary="They agreed on Friday." />);
    expect(screen.getByRole("note")).toHaveTextContent("1 earlier message is replaced by a summary");
    const toggle = screen.getByRole("button", { name: "Show the summary" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(toggle);
    expect(screen.getByRole("region", { name: "Earlier conversation summary" })).toHaveTextContent("They agreed on Friday.");
    expect(screen.getByRole("button", { name: "Hide the summary" })).toHaveAttribute("aria-expanded", "true");
  });

  it("explains a failed summary and a retry", () => {
    render(<ContextMarker dropped={2} summary={null} summaryFailed retried />);
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("Summarizing failed");
    expect(note).toHaveTextContent("The server rejected the first attempt");
  });

  it("uses Lithuanian plural forms", async () => {
    await i18n.changeLanguage("lt");
    const { rerender } = render(<ContextMarker dropped={1} summary={null} />);
    expect(screen.getByRole("note")).toHaveTextContent("1 ankstesnė žinutė neįtraukta");
    rerender(<ContextMarker dropped={3} summary={null} />);
    expect(screen.getByRole("note")).toHaveTextContent("3 ankstesnės žinutės neįtrauktos");
    rerender(<ContextMarker dropped={12} summary={null} />);
    expect(screen.getByRole("note")).toHaveTextContent("12 ankstesnių žinučių neįtraukta");
  });
});

describe("ContextIndicator", () => {
  const estimate: ContextEstimate = {
    window: 8192,
    window_source: "server",
    strategy: "sliding",
    exact: true,
    used: 3000,
    dropped: 0,
    breakdown: { system: 200, summary: 0, latest: 0, pinned: 300, history: 2500, reserved: 1024 },
  } as ContextEstimate;

  it("shows usage and a breakdown that includes the draft", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(estimate)));
    useChat.setState({ convs: { c1: { messages: TREE, activePath: ["u1", "a1"], loaded: true, streaming: false, webSources: {} } as never } });
    useUI.setState({ draftChars: { key: "c1", n: 350 } });
    render(<ContextIndicator conversationId="c1" />);
    const button = await screen.findByRole("button", { name: /^Context window usage: 3\.1K \/ 8\.2K$/ });
    await userEvent.click(button);
    expect(screen.getByText("Pinned messages")).toBeInTheDocument();
    expect(screen.getByText("Your draft")).toBeInTheDocument();
    expect(screen.getByText(/Context window: 8,192 tokens \(reported by the server\)/)).toBeInTheDocument();
    expect(screen.getByText(/Counted with the model's own tokenizer/)).toBeInTheDocument();
    useUI.setState({ draftChars: { key: "", n: 0 } });
  });

  it("regression: never shows another conversation's numbers when you switch, even while it is answering", async () => {
    // Short chat c1 answers at once; long chat c2 is generating and answers late.
    let releaseLong: (r: Response) => void = () => {};
    const fetchMock = vi.fn((url: string) =>
      url.includes("/c2/") ? new Promise<Response>((r) => (releaseLong = r)) : Promise.resolve(json({ ...estimate, used: 76 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    useChat.setState({
      convs: {
        c1: { messages: TREE, activePath: ["u1", "a1"], loaded: true, streaming: false, webSources: {} },
        c2: { messages: TREE, activePath: ["u1", "a1"], loaded: true, streaming: true, webSources: {} },
      } as never,
    });
    useUI.setState({ draftChars: { key: "c1", n: 0 } });
    const { rerender } = render(<ContextIndicator conversationId="c1" />);
    expect(await screen.findByRole("button", { name: /: 76 \/ 8\.2K$/ })).toBeInTheDocument();
    rerender(<ContextIndicator conversationId="c2" />);
    // c2's estimate is still on its way: nothing is shown rather than c1's 76.
    expect(screen.queryByTestId("context-indicator")).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/c2/"), expect.anything()); // fetched while it streams
    await act(async () => releaseLong(json({ ...estimate, used: 5000 })));
    expect(await screen.findByRole("button", { name: /: 5K \/ 8\.2K$/ })).toBeInTheDocument();
    // Back to c1: its own numbers straight away, and c1's draft never counts in c2.
    rerender(<ContextIndicator conversationId="c1" />);
    expect(screen.getByRole("button", { name: /: 76 \/ 8\.2K$/ })).toBeInTheDocument();
    useUI.setState({ draftChars: { key: "c1", n: 3500 } });
    rerender(<ContextIndicator conversationId="c2" />);
    expect(await screen.findByRole("button", { name: /: 5K \/ 8\.2K$/ })).toBeInTheDocument();
    useUI.setState({ draftChars: { key: "", n: 0 } });
  });
});

describe("localized context and branch errors", () => {
  it("translates stored context errors with their numbers", async () => {
    expect(providerErrorText("context.too_large", { needed: 9000, available: 4000 }, "fallback")).toContain("about 9000 tokens, but only 4000");
    await i18n.changeLanguage("lt");
    expect(providerErrorText("context.too_large", { needed: 9000, available: 4000 }, "fallback")).not.toBe("fallback");
    expect(providerErrorText("context_exceeded", { n_prompt_tokens: 5000, n_ctx: 4096 }, "fallback")).toContain("4096");
  });

  it("translates a missing branch", () => {
    expect(errorText(new ApiError("Message not found", 404, "branch.not_found"))).toBe("That message is no longer in this conversation.");
  });
});
