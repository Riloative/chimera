import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TooltipProvider } from "@/components/ui";
import type { Message } from "@/lib/types";

// Count how often each message row renders (the real rows pull in Markdown etc.).
const renders = new Map<string, number>();
vi.mock("@/components/MessageView", () => {
  const row = ({ message }: { message: Message }) => {
    renders.set(message.id, (renders.get(message.id) ?? 0) + 1);
    return <div data-testid="row">{message.content}</div>;
  };
  return { AssistantMessage: row, UserMessage: row };
});

const { MessageList, INITIAL_MESSAGES } = await import("./ChatPage");

function chain(n: number): Message[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `m${i}`,
    conversation_id: "c1",
    parent_id: i ? `m${i - 1}` : null,
    position: i,
    role: i % 2 ? "assistant" : "user",
    content: `Message ${i}`,
    status: "complete",
    created_at: `2026-11-18T10:${String(Math.floor(i / 60)).padStart(2, "0")}:${String(i % 60).padStart(2, "0")}Z`,
  })) as Message[];
}

function List({ messages }: { messages: Message[] }) {
  // New handler closures every render, exactly as ChatPage passes them.
  return (
    <MessageList
      messages={messages}
      tree={messages}
      loading={false}
      busy={false}
      showReasoning
      expandSteps={false}
      onSwitch={() => {}}
      onTogglePin={() => {}}
      onChangeStrategy={() => {}}
      onRegenerate={() => {}}
      onEdit={() => {}}
      onDelete={() => {}}
    >
      <div />
    </MessageList>
  );
}

const wrap = (ui: React.ReactNode) => (
  <MemoryRouter>
    <TooltipProvider>{ui}</TooltipProvider>
  </MemoryRouter>
);

beforeEach(() => renders.clear());

describe("MessageList", () => {
  it("re-renders only the streaming message when a token arrives (not the whole conversation)", () => {
    const messages = chain(40);
    const { rerender } = render(wrap(<List messages={messages} />));
    expect(renders.get("m0")).toBe(1);
    // A delta: the store replaces only the last message object (and the arrays).
    for (const token of [" and", " more", " text"]) {
      const last = messages.at(-1)!;
      messages[messages.length - 1] = { ...last, content: last.content + token };
      rerender(wrap(<List messages={[...messages]} />));
    }
    expect(renders.get("m0")).toBe(1);
    expect(renders.get("m38")).toBe(1);
    expect(renders.get("m39")).toBe(4);
    expect(screen.getByText("Message 39 and more text")).toBeInTheDocument();
  });

  it("opens a long conversation with only the latest messages, and shows earlier ones on request", () => {
    render(wrap(<List messages={chain(150)} />));
    expect(screen.getAllByTestId("row")).toHaveLength(INITIAL_MESSAGES);
    expect(screen.getByText("Message 149")).toBeInTheDocument();
    expect(screen.queryByText("Message 0")).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: `Show ${150 - INITIAL_MESSAGES} earlier messages` });
    act(() => fireEvent.click(more));
    expect(screen.getAllByTestId("row")).toHaveLength(140);
    act(() => fireEvent.click(screen.getByRole("button", { name: "Show 10 earlier messages" })));
    expect(screen.getAllByTestId("row")).toHaveLength(150);
    expect(screen.getByText("Message 0")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /earlier message/ })).not.toBeInTheDocument();
  });

  it("renders a short conversation completely, without the button", () => {
    render(wrap(<List messages={chain(5)} />));
    expect(screen.getAllByTestId("row")).toHaveLength(5);
    expect(screen.queryByTestId("show-earlier")).not.toBeInTheDocument();
  });
});
