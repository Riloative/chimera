import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { useConversations } from "@/stores/conversations";
import { useUI } from "@/stores/ui";
import { SearchDialog } from "./SearchDialog";

const conv = (id: string, title: string) =>
  ({ id, title, title_source: "model", created_at: "2026-11-18T09:00:00Z", updated_at: "2026-11-18T09:00:00Z" }) as Conversation;

function deferred<T>() {
  let resolve: (v: T) => void = () => {};
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

const type = (text: string) => fireEvent.change(screen.getByRole("combobox"), { target: { value: text } });

beforeEach(() => {
  vi.useFakeTimers();
  useConversations.setState({ items: [], loaded: true });
  useUI.getState().setSearchOpen(true);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  useUI.getState().setSearchOpen(false);
});

const mount = () =>
  render(
    <MemoryRouter>
      <SearchDialog />
    </MemoryRouter>,
  );

describe("search dialog", () => {
  it("shows the results of the latest query even when an older request answers last", async () => {
    const slow = deferred<Conversation[]>();
    const fast = deferred<Conversation[]>();
    vi.spyOn(api, "searchConversations").mockImplementation((q: string) => (q === "kau" ? slow.promise : fast.promise));
    mount();
    type("kau");
    act(() => vi.advanceTimersByTime(200));
    type("kaunas");
    act(() => vi.advanceTimersByTime(200));
    await act(async () => fast.resolve([conv("new", "Trip to Kaunas")]));
    await act(async () => slow.resolve([conv("old", "Kaunas café (stale)")]));
    expect(screen.getByText("Trip to Kaunas")).toBeInTheDocument();
    expect(screen.queryByText("Kaunas café (stale)")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument(); // not loading any more
  });

  it("stops the loading indicator when the box is cleared before the search runs", () => {
    vi.spyOn(api, "searchConversations").mockImplementation(() => new Promise(() => {}));
    mount();
    type("vilnius");
    expect(screen.getByRole("status")).toBeInTheDocument();
    type("");
    act(() => vi.advanceTimersByTime(500));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows a conversation's current title, not the one it had when the search ran", async () => {
    useConversations.setState({ items: [conv("c1", "New conversation")] });
    vi.spyOn(api, "searchConversations").mockResolvedValue([conv("c1", "New conversation")]);
    mount();
    type("new");
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("option", { name: /New conversation/ })).toBeInTheDocument();
    act(() => useConversations.getState().upsert({ id: "c1", title: "Planning a trip", title_source: "model" }));
    expect(screen.getByRole("option", { name: /Planning a trip/ })).toBeInTheDocument();
  });
});
