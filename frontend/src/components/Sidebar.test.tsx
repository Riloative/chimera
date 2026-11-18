import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { useAuth } from "@/stores/auth";
import { useConversations } from "@/stores/conversations";
import { Sidebar } from "./Sidebar";
import { TooltipProvider } from "./ui";

const conv = { id: "c1", title: "Testas-testas?", title_source: "fallback", pinned: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString() } as Conversation;

const mount = () =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <Sidebar />
      </TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  useConversations.setState({ items: [conv], loaded: true, hasMore: false });
  useAuth.setState({ user: { id: "u1", username: "audit", email: "a@example.com", role: "admin" } as never });
});
afterEach(() => vi.restoreAllMocks());

describe("sidebar", () => {
  it("keeps what the user is typing when the generated title arrives during a rename", async () => {
    const update = vi.spyOn(api, "updateConversation").mockImplementation(async (_id, data) => ({ ...conv, ...data, title_source: "user" }) as Conversation);
    mount();
    fireEvent.doubleClick(screen.getByRole("link", { name: "Testas-testas?" }));
    const field = screen.getByRole("textbox", { name: "Conversation title" });
    fireEvent.change(field, { target: { value: "Kelionė į Kauną" } });
    act(() => useConversations.getState().upsert({ id: "c1", title: "Mock Conversation Title", title_source: "model" }));
    expect(field).toHaveValue("Kelionė į Kauną");
    fireEvent.keyDown(field, { key: "Enter" });
    fireEvent.blur(field);
    await act(async () => {});
    expect(update).toHaveBeenCalledTimes(1); // Enter then blur: saved once
    expect(update).toHaveBeenCalledWith("c1", { title: "Kelionė į Kauną" });
    expect(screen.getByRole("link", { name: "Kelionė į Kauną" })).toBeInTheDocument();
  });

  it("names the account menu by its text, with the initials as decoration", () => {
    mount();
    const button = screen.getByRole("button", { name: "Account menu for audit" });
    expect(button.querySelector("[aria-hidden]")).toHaveTextContent("AU");
  });

  it("lists conversations in a labelled navigation landmark and offers older ones", () => {
    useConversations.setState({ hasMore: true });
    mount();
    const nav = screen.getByRole("navigation", { name: "Conversations" });
    expect(nav).toContainElement(screen.getByRole("link", { name: "Testas-testas?" }));
    expect(screen.getByRole("button", { name: "Show older conversations" })).toBeInTheDocument();
  });
});
