import { act, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import { providerErrorText } from "@/lib/errors";
import type { AppSettings, Message, SearchMode, WebRef, WebSource, WebStatus, WebUserSettings } from "@/lib/types";
import { useChat } from "@/stores/chat";
import { useSettings } from "@/stores/settings";
import { useWeb } from "@/stores/web";
import { Composer, type ComposerProps } from "./Composer";
import { Markdown } from "./Markdown";
import { TooltipProvider } from "./ui";
import { citationTargets, LinkStatus, visibleRefs, WebSourceList, WebSteps } from "./WebParts";

const PREFS: WebUserSettings = {
  auto_read_links: true,
  search_default: "off",
  results: 5,
  expand_steps: false,
  task_provider_id: null,
  task_model: null,
  privacy_acknowledged: true,
};
const STATUS: WebStatus = { enabled: true, search_available: true, auto_available: true, engine: "searxng", max_urls_per_message: 2, max_results: 8, is_admin: false };

function wrap(ui: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{ui}</TooltipProvider>
    </MemoryRouter>,
  );
}

/** The composer with its conversation's search mode held like ChatPage holds it (stored per conversation). */
function Harness({ initial, onSend, onMode }: { initial: SearchMode; onSend: ComposerProps["onSend"]; onMode: (m: SearchMode) => void }) {
  const [mode, setMode] = useState<SearchMode>(initial);
  return (
    <Composer
      onSend={onSend}
      model={{ providerId: "p", model: "m" }}
      onModelChange={() => {}}
      visionSupported={false}
      conversationKey="c1"
      searchMode={mode}
      onSearchModeChange={(m) => {
        setMode(m);
        onMode(m);
      }}
    />
  );
}

function setupComposer(status: WebStatus | null, prefs: Partial<WebUserSettings> = {}, mode: SearchMode = "off") {
  const update = vi.fn().mockResolvedValue(null);
  useSettings.setState({ settings: { enter_to_send: true, web: { ...PREFS, ...prefs } } as AppSettings, update });
  useWeb.setState({ status, override: {}, draftMode: null });
  const onSend = vi.fn().mockResolvedValue(undefined);
  const onMode = vi.fn();
  wrap(<Harness initial={mode} onSend={onSend} onMode={onMode} />);
  return { onSend, update, onMode, input: screen.getByLabelText("Message") as HTMLTextAreaElement };
}

async function chooseInMenu(name: string | RegExp) {
  await userEvent.click(screen.getByRole("button", { name: /^(Web search options|Paieškos internete parinktys)$/ }));
  const item = await screen.findAllByRole("menuitem", { name }).catch(() => screen.findAllByRole("menuitemradio", { name }));
  await userEvent.click(item[0]);
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
    expect(onSend).toHaveBeenCalledWith(expect.any(String), [], { urls: ["https://a.example.com/x"], web_search: null });
  });

  it("asks before reading links when auto-read is off", async () => {
    const { onSend, input } = setupComposer(STATUS, { auto_read_links: false });
    await userEvent.type(input, "see https://a.example.com");
    const chip = screen.getByRole("button", { name: "Read https://a.example.com" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(chip);
    await userEvent.click(input);
    await userEvent.keyboard("{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: ["https://a.example.com"], web_search: null });
  });

  it("the search button turns web search on for the whole conversation, and it stays on", async () => {
    const { onSend, onMode, input } = setupComposer(STATUS);
    const toggle = screen.getByTestId("search-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(toggle).toHaveAccessibleName("Search the web in this conversation");
    await userEvent.click(toggle);
    expect(onMode).toHaveBeenLastCalledWith("on");
    expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
    await userEvent.type(input, "latest news{Enter}");
    // The conversation's mode decides: no per-message value is sent.
    expect(onSend.mock.calls[0][2]).toEqual({ urls: [], web_search: null });
    await userEvent.type(input, "and more{Enter}");
    expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
  });

  it("a one-off choice applies to the next message only, and says so", async () => {
    const { onSend, onMode, input } = setupComposer(STATUS);
    await chooseInMenu("Search the web for the next message");
    expect(screen.getByTestId("search-control")).toHaveAttribute("data-effective", "on");
    expect(screen.getByText("Web search for the next message only")).toBeInTheDocument();
    expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "false"); // the conversation stays off
    await userEvent.type(input, "festival dates{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: [], web_search: true });
    await vi.waitFor(() => expect(screen.getByTestId("search-control")).toHaveAttribute("data-effective", "off"));
    expect(screen.queryByText("Web search for the next message only")).toBeNull();
    expect(onMode).not.toHaveBeenCalled();
  });

  it("can skip search for one message in a conversation that searches", async () => {
    const { onSend, input } = setupComposer(STATUS, {}, "on");
    await chooseInMenu("Don't search for the next message");
    expect(screen.getByText("No web search for the next message")).toBeInTheDocument();
    await userEvent.type(input, "thanks{Enter}");
    expect(onSend.mock.calls[0][2]).toEqual({ urls: [], web_search: false });
    await vi.waitFor(() => expect(screen.getByTestId("search-control")).toHaveAttribute("data-effective", "on"));
  });

  it("offers Auto when the server allows it", async () => {
    const { onMode } = setupComposer(STATUS);
    await chooseInMenu(/^Auto/);
    expect(onMode).toHaveBeenLastCalledWith("auto");
    const toggle = screen.getByTestId("search-toggle");
    expect(toggle).toHaveAccessibleName("Auto web search in this conversation");
    expect(toggle).toHaveTextContent("Auto");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle); // the button turns it off
    expect(onMode).toHaveBeenLastCalledWith("off");
  });

  it("hides Auto when an administrator turned it off, and treats it as off", async () => {
    setupComposer({ ...STATUS, auto_available: false }, {}, "auto");
    expect(screen.getByTestId("search-control")).toHaveAttribute("data-mode", "off");
    await userEvent.click(screen.getByRole("button", { name: "Web search options" }));
    expect(await screen.findByRole("menuitemradio", { name: /^On/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitemradio", { name: /^Auto/ })).toBeNull();
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
    expect(onSend.mock.calls[0][2]).toEqual({ urls: null, web_search: null });
  });

  it("shows the privacy notice the first time search is turned on", async () => {
    const { update, onMode } = setupComposer(STATUS, { privacy_acknowledged: false });
    await userEvent.click(screen.getByTestId("search-toggle"));
    const dialog = await screen.findByRole("dialog");
    expect(onMode).not.toHaveBeenCalled(); // nothing changes before the notice is accepted
    expect(within(dialog).getByText(/self-hosted|own SearXNG/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Turn on web search" }));
    expect(screen.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
    expect(onMode).toHaveBeenLastCalledWith("on");
    expect(update).toHaveBeenCalledWith({ web: expect.objectContaining({ privacy_acknowledged: true }) });
  });

  it("works in Lithuanian at once", async () => {
    setupComposer(STATUS, {}, "on");
    await act(() => i18n.changeLanguage("lt"));
    expect(screen.getByTestId("search-toggle")).toHaveAccessibleName("Paieška internete šiame pokalbyje");
    expect(screen.getByText("Paieška")).toBeInTheDocument();
    await chooseInMenu("Kitai žinutei neieškoti");
    expect(screen.getByText("Kitai žinutei internete neieškoti")).toBeInTheDocument();
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
    const toggle = screen.getByRole("button", { description: "Show web activity" });
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

describe("headless browser", () => {
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  afterEach(async () => {
    vi.unstubAllGlobals();
    await act(() => i18n.changeLanguage("en"));
  });

  function openShell(available: boolean, extra: Partial<WebSource> = {}) {
    const shell = SOURCE("s9", 1, { origin: "pasted", status: "error", error_code: "web.needs_javascript", chars: 0, ...extra });
    useWeb.setState({ status: { ...STATUS, browser_available: available }, override: {} });
    useChat.setState({ convs: {} });
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/refetch")) {
        expect(JSON.parse(String(init?.body))).toEqual({ loader: "browser" });
        return json({ ok: true, source: { ...shell, status: "ok", error_code: null, loader: "browser", chars: 300 } });
      }
      return json({ ...shell, content: url.includes("s9") && fetchMock.mock.calls.length > 1 ? "Rendered text" : "" });
    });
    vi.stubGlobal("fetch", fetchMock);
    wrap(<WebSourceList refs={[{ n: 1, source_id: "s9", included: true }]} sources={{ s9: shell }} conversationId="c1" />);
    return fetchMock;
  }

  it("offers to render a JavaScript page with the browser and marks the result", async () => {
    const fetchMock = openShell(true);
    await userEvent.click(screen.getByRole("button", { name: /View the text that was read/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("needs JavaScript");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Try “Render with browser”.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Render with browser" }));
    expect(fetchMock).toHaveBeenCalledWith("/api/web/sources/s9/refetch", expect.objectContaining({ method: "POST" }));
    expect(await within(dialog).findByText("Rendered with the headless browser")).toBeInTheDocument();
  });

  it("hides the action when no browser is available and explains a failed render", async () => {
    openShell(false, { error_params: { browser: "web.browser_unavailable" } });
    await userEvent.click(screen.getByRole("button", { name: /View the text that was read/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "Render with browser" })).toBeNull();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("The headless browser couldn't help either: The headless browser is not available right now.");
  });

  it("is translated", async () => {
    await act(() => i18n.changeLanguage("lt"));
    openShell(true);
    await userEvent.click(screen.getByRole("button", { name: /Peržiūrėti|tekst/i }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Atvaizduoti naršykle" })).toBeInTheDocument();
    expect(providerErrorText("web.browser_limit", {}, "x")).toContain("valandinį limitą");
    expect(providerErrorText("web.browser_blocked", {}, "x")).toContain("neleidžiamas");
  });
});

describe("sources under an answer", () => {
  const withRefs = (refs: WebRef[], used: number[] = []) =>
    ({ ...MESSAGE, web: { ...MESSAGE.web, refs, citations: { used, invalid: [] } } }) as Message;

  it("lists this turn's sources and only the earlier pages the answer cites", () => {
    const msg = withRefs(
      [
        { n: 1, source_id: "s1", included: true, earlier: true },
        { n: 2, source_id: "s2", included: true, earlier: true },
        { n: 3, source_id: "s3", included: true },
      ],
      [2],
    );
    expect(visibleRefs(msg).map((r) => r.n)).toEqual([2, 3]);
    // Citation numbers stay stable: [2] still resolves to the earlier page.
    expect(citationTargets(msg, SOURCES)[2].url).toBe("https://site2.example/page");
  });

  it("follows the server's decision once the answer is final", () => {
    const msg = withRefs([
      { n: 1, source_id: "s1", included: true, earlier: true, shown: false },
      { n: 2, source_id: "s2", included: true, shown: true },
    ]);
    expect(visibleRefs(msg).map((r) => r.n)).toEqual([2]);
  });

  it("treats refs stored before 0.4.1 (no flags) as this turn's", () => {
    expect(visibleRefs(MESSAGE).map((r) => r.n)).toEqual([1, 2, 3]);
  });

  it("shows no Sources block when nothing belongs to the answer", () => {
    const msg = withRefs([{ n: 1, source_id: "s1", included: true, earlier: true }]);
    wrap(<WebSourceList refs={visibleRefs(msg)} sources={SOURCES} conversationId="c1" />);
    expect(screen.queryByTestId("web-sources")).toBeNull();
  });

  it("counts only this turn's sources in the steps summary", () => {
    const msg = withRefs([
      { n: 1, source_id: "s1", included: true, earlier: true },
      { n: 2, source_id: "s3", included: true },
    ]);
    wrap(<WebSteps message={msg} sources={SOURCES} defaultOpen={false} />);
    expect(screen.getByRole("button", { description: "Show web activity" })).toHaveTextContent("Used 1 source");
  });
});

describe("failed sources say why", () => {
  afterEach(async () => {
    await act(() => i18n.changeLanguage("en"));
  });

  const FAILED: [string, Record<string, unknown>, RegExp][] = [
    ["web.blocked_address", {}, /private or local network/],
    ["web.not_found", { http_status: 404 }, /page was not found/],
    ["web.timeout", {}, /took too long/],
    ["web.needs_javascript", {}, /needs JavaScript/],
    ["web.browser_blocked", {}, /not allowed/],
    ["web.browser_loading", {}, /loading screen/],
  ];

  it.each(FAILED)("%s shows its reason, never the context-limit text, and matches the chip", (code, params, text) => {
    useChat.setState({ convs: {} });
    const failed = SOURCE("f1", 1, { origin: "pasted", status: "error", error_code: code, error_params: params, chars: 0 });
    wrap(
      <>
        <WebSourceList refs={[{ n: 1, source_id: "f1", included: false }]} sources={{ f1: failed }} conversationId="c1" />
        <LinkStatus sources={[failed]} conversationId="c1" />
      </>,
    );
    const reason = screen.getByTestId("source-reason");
    expect(reason).toHaveTextContent(text);
    expect(screen.getByTestId("web-sources")).not.toHaveTextContent("context limit");
    const chip = screen.getByTestId("link-status").querySelector("button")!;
    expect(chip.getAttribute("aria-label")).toContain(reason.getAttribute("title"));
  });

  it("keeps 'left out' only for pages that were read but didn't fit", () => {
    wrap(<WebSourceList refs={[{ n: 1, source_id: "s1", included: false }]} sources={{ s1: SOURCE("s1", 1) }} conversationId="c1" />);
    expect(screen.getByTestId("web-sources")).toHaveTextContent("Left out because of the model's context limit.");
    expect(screen.queryByTestId("source-reason")).toBeNull();
  });

  it("is translated", async () => {
    await act(() => i18n.changeLanguage("lt"));
    const failed = SOURCE("f1", 1, { status: "error", error_code: "web.blocked_address", chars: 0 });
    wrap(<WebSourceList refs={[{ n: 1, source_id: "f1", included: false }]} sources={{ f1: failed }} conversationId="c1" />);
    expect(screen.getByTestId("source-reason")).toHaveTextContent(/privat/);
    expect(providerErrorText("web.browser_loading", {}, "x")).toContain("įkėlimo ekraną");
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
