import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import type { Message } from "@/lib/types";
import { AssistantMessage } from "./MessageView";
import { ThinkingIndicator } from "./ThinkingIndicator";
import { Spinner, TooltipProvider } from "./ui";

const streaming = {
  id: "a1",
  conversation_id: "c1",
  parent_id: "u1",
  position: 1,
  role: "assistant",
  content: "",
  reasoning: "",
  status: "streaming",
  error: null,
  provider_id: "p",
  model: "qwen-local",
  attachments: [],
  sources: [],
  metrics: {},
  created_at: "2026-11-18T10:00:00Z",
  updated_at: "2026-11-18T10:00:00Z",
} as Message;

const wrap = (ui: React.ReactNode) =>
  render(
    <MemoryRouter>
      <TooltipProvider>{ui}</TooltipProvider>
    </MemoryRouter>,
  );

afterEach(async () => {
  cleanup();
  await act(() => i18n.changeLanguage("en"));
});

describe("ThinkingIndicator", () => {
  it("is decorative: hidden from assistive technology, CSS-only (no timers)", () => {
    vi.useFakeTimers();
    const { container } = render(<ThinkingIndicator />);
    const el = screen.getByTestId("thinking-indicator");
    expect(el).toHaveAttribute("aria-hidden", "true");
    expect(el.querySelector(".thinking-orbit")).not.toBeNull();
    expect(el.querySelector(".thinking-core")).not.toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    expect(container.querySelector("svg")).toBeNull(); // an HTML element rotates, not an SVG child
    vi.useRealTimers();
  });

  it("can drop the core (stop button)", () => {
    render(<ThinkingIndicator core={false} />);
    expect(screen.getByTestId("thinking-indicator").querySelector(".thinking-core")).toBeNull();
  });

  it("keeps the localized status text next to it while waiting for the first token", () => {
    wrap(<AssistantMessage message={streaming} isLast busy showReasoning onRegenerate={() => {}} onDelete={() => {}} />);
    const status = screen.getByRole("status", { name: "Waiting for the model" });
    expect(within(status).getByText("Thinking…")).toBeInTheDocument();
    expect(within(status).getByTestId("thinking-indicator")).toHaveAttribute("aria-hidden", "true");
  });

  it("marks live reasoning with the same indicator", () => {
    wrap(<AssistantMessage message={{ ...streaming, reasoning: "Let me see" }} isLast busy showReasoning onRegenerate={() => {}} onDelete={() => {}} />);
    const toggle = screen.getByRole("button", { name: "Thinking…" });
    expect(within(toggle).getByTestId("thinking-indicator")).toBeInTheDocument();
  });

  it("is used for loading states, which announce a localized label", async () => {
    render(<Spinner />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    cleanup();
    await act(() => i18n.changeLanguage("lt"));
    render(<Spinner />);
    expect(screen.getByRole("status")).toHaveTextContent("Įkeliama");
  });
});
