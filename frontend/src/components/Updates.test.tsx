import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import type { UpdateView } from "@/lib/types";
import { SettingsPage } from "@/pages/SettingsPage";
import { useAuth } from "@/stores/auth";
import { Route, Routes } from "react-router-dom";
import { TooltipProvider } from "./ui";
import { UpdateNotice, useUpdates, useUpdatesReady } from "./UpdateNotice";

/** Like AppLayout: the page waits for the update check, then renders the notice and the content together. */
function Shell() {
  const ready = useUpdatesReady();
  return ready ? (
    <>
      <UpdateNotice />
      <p>Page content</p>
    </>
  ) : (
    <p>Loading the page</p>
  );
}

const ADMIN = { id: "a", username: "admin", email: "a@x.io", role: "admin", language: "en", active: true, must_change_password: false, last_login_at: null, created_at: "" };
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const VIEW: UpdateView = { enabled: true, asked: true, current: "1.0.0", available: null, last_check: { checked_at: "2026-10-16T09:00:00Z", ok: true, error: null } };

function mockApi(view: UpdateView) {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body as string | undefined });
      if (url === "/api/admin/updates") {
        if (init?.method === "PATCH") return json({ ...view, asked: true, enabled: JSON.parse(String(init.body)).enabled });
        return json(view);
      }
      if (url === "/api/info") return json({ version: "1.0.0", commit: "abc1234", telemetry: false, registration_mode: "closed" });
      return json({});
    }),
  );
  return calls;
}

beforeEach(() => useUpdates.setState({ view: null, settled: false }));

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  try {
    localStorage.clear();
  } catch {
    /* ignore */
  }
  await act(() => i18n.changeLanguage("en"));
});

describe("update notice (administrators)", () => {
  it("asks once at first run and saves the answer", async () => {
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    const calls = mockApi({ ...VIEW, asked: false, enabled: false });
    render(<Shell />);
    expect(await screen.findByText("Check for new Chimera versions?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "No, thanks" }));
    expect(calls.find((c) => c.body)?.body).toBe(JSON.stringify({ enabled: false }));
    expect(screen.queryByTestId("update-ask")).toBeNull();
  });

  it("shows a quiet, dismissible notice with release notes and instructions", async () => {
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    mockApi({ ...VIEW, available: { version: "1.1.0", url: "https://github.com/Riloative/chimera/releases/tag/v1.1.0" } });
    render(<Shell />);
    const notice = await screen.findByTestId("update-notice");
    expect(notice).toHaveTextContent("Chimera 1.1.0 is available.");
    expect(screen.getByRole("link", { name: "Release notes" })).toHaveAttribute("href", "https://github.com/Riloative/chimera/releases/tag/v1.1.0");
    expect(screen.getByRole("link", { name: "How to update" })).toHaveAttribute("href", expect.stringContaining("DEPLOYMENT.md#updating"));
    await userEvent.click(screen.getByRole("button", { name: "Hide until the next version" }));
    expect(screen.queryByTestId("update-notice")).toBeNull();
  });

  it("is part of the first frame: the content never moves down when it appears", async () => {
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    let answer: (r: Response) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((r) => (answer = r))));
    render(<Shell />);
    expect(screen.getByText("Loading the page")).toBeInTheDocument();
    expect(screen.queryByText("Page content")).toBeNull();
    await act(async () => answer(json({ ...VIEW, asked: false, enabled: false })));
    // Both arrive in the same render.
    expect(screen.getByText("Page content")).toBeInTheDocument();
    expect(screen.getByTestId("update-ask")).toBeInTheDocument();
  });

  it("doesn't hold the page up for long when the check is slow", async () => {
    vi.useFakeTimers();
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    render(<Shell />);
    await act(async () => vi.advanceTimersByTime(900));
    expect(screen.getByText("Page content")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("shows nothing to regular users and makes no request", () => {
    useAuth.setState({ status: "authenticated", user: { ...ADMIN, role: "user" } as never });
    const calls = mockApi(VIEW);
    render(<Shell />);
    expect(screen.getByText("Page content")).toBeInTheDocument(); // no waiting either
    expect(screen.queryByTestId("update-ask")).toBeNull();
    expect(calls).toEqual([]);
  });

  it("is translated", async () => {
    await act(() => i18n.changeLanguage("lt"));
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    mockApi({ ...VIEW, available: { version: "1.1.0", url: "https://github.com/Riloative/chimera/releases/tag/v1.1.0" } });
    render(<Shell />);
    expect(await screen.findByTestId("update-notice")).toHaveTextContent("Išleista Chimera 1.1.0.");
  });
});

describe("Settings → About", () => {
  const renderAbout = () =>
    render(
      <MemoryRouter initialEntries={["/settings/about"]}>
        <TooltipProvider>
          <Routes>
            <Route path="/settings/:section" element={<SettingsPage />} />
          </Routes>
        </TooltipProvider>
      </MemoryRouter>,
    );

  it("shows the version and build to everyone", async () => {
    useAuth.setState({ status: "authenticated", user: { ...ADMIN, role: "user" } as never });
    mockApi(VIEW);
    renderAbout();
    expect(await screen.findByText("1.0.0")).toBeInTheDocument();
    expect(screen.getByText("abc1234")).toBeInTheDocument();
    expect(screen.queryByText("Check for updates")).toBeNull();
  });

  it("lets administrators turn the check on and see its last result", async () => {
    useAuth.setState({ status: "authenticated", user: ADMIN as never });
    mockApi({ ...VIEW, last_check: { checked_at: "2026-10-16T09:00:00Z", ok: false, error: "web.timeout" } });
    renderAbout();
    expect(await screen.findByRole("switch", { name: "Check for updates" })).toBeChecked();
    expect(screen.getByTestId("update-last-check")).toHaveTextContent("The last check failed");
    expect(screen.getByTestId("update-last-check")).toHaveTextContent("took too long");
  });
});
