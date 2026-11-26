import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type Page } from "@playwright/test";

// Web search per conversation (1.2): stored on the server, a one-off choice for
// the next message, regenerate following the conversation, Auto, Lithuanian.
// A dedicated account keeps the administrator's first-use privacy notice for web.spec.ts.

const WEB = "http://127.0.0.1:18090";
const composer = (page: Page) => page.getByRole("textbox", { name: /^(Message|Žinutė)$/ });

async function adminWeb(browser: Browser, settings: Record<string, unknown>) {
  const context = await browser.newContext({ storageState: "test-results/admin-state.json" });
  const page = await context.newPage();
  await page.goto("/");
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")?.value ?? "";
  const resp = await page.request.patch("/api/admin/web", { headers: { "X-CSRF-Token": csrf }, data: { settings } });
  expect(resp.ok(), await resp.text()).toBeTruthy();
  await context.close();
}

let accounts = 0;
async function signUp(browser: Browser, language: "en" | "lt" = "en") {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const name = `searcher${Date.now().toString(36)}${accounts++}`;
  await context.request.get("/api/auth/status");
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")!.value;
  const pw = "quiet harbour lantern 5521";
  const resp = await context.request.post("/api/auth/register", {
    headers: { "X-CSRF-Token": csrf },
    data: { email: `${name}@example.com`, username: name, password: pw, password_confirm: pw, language },
  });
  expect(resp.ok(), await resp.text()).toBeTruthy();
  return context;
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("send-button")).toBeVisible({ timeout: 20_000 }); // the answer has finished
}

const lastAnswer = (page: Page) => page.getByRole("list", { name: "Messages" }).locator(":scope > li").last();

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  await adminWeb(browser, { enabled: true, engine: "searxng", searxng_url: WEB, fetch_allowlist: ["127.0.0.1"], auto_search: true });
});

test.afterAll(async ({ browser }) => {
  await adminWeb(browser, { enabled: false, engine: null, searxng_url: "", fetch_allowlist: [] });
});

test("search stays on for the conversation, across reloads; a one-off skips one message; regenerate follows", async ({ browser }) => {
  const context = await signUp(browser);
  const page = await context.newPage();
  await page.goto("/");
  const toggle = page.getByTestId("search-toggle");
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await toggle.click();
  await page.getByRole("dialog").getByRole("button", { name: "Turn on web search" }).click(); // first use only
  await expect(toggle).toHaveAttribute("aria-pressed", "true");

  // A new conversation is created with search on.
  await send(page, "vilnius festival in March");
  await expect(page).toHaveURL(/\/c\/[0-9a-f]+$/);
  await expect(lastAnswer(page).getByTestId("web-steps")).toContainText(/Used \d sources/);
  await page.reload();
  await expect(page.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");

  // The menu is accessible (light and dark).
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await page.getByRole("button", { name: "Web search options" }).click();
    await expect(page.getByRole("menu")).toBeVisible();
    const found = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(found.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), scheme).toEqual([]);
    await page.keyboard.press("Escape");
  }
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "no-preference" });

  // One message without search; the conversation stays on.
  await page.getByRole("button", { name: "Web search options" }).click();
  await page.getByRole("menuitem", { name: "Don't search for the next message" }).click();
  await expect(page.getByText("No web search for the next message")).toBeVisible();
  await expect(page.getByTestId("search-control")).toHaveAttribute("data-effective", "off");
  await send(page, "thanks, that helps");
  await expect(lastAnswer(page).getByTestId("web-steps")).toHaveCount(0);
  await expect(page.getByText("No web search for the next message")).toHaveCount(0);
  await expect(page.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");

  // Regenerating that answer follows the conversation: now it searches.
  await lastAnswer(page).hover();
  await lastAnswer(page).getByRole("button", { name: "Regenerate" }).click();
  await expect(lastAnswer(page).getByTestId("web-steps")).toContainText(/Used \d sources/, { timeout: 20_000 });

  // Lithuanian: the same state, translated.
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")!.value;
  await page.request.patch("/api/account", { headers: { "X-CSRF-Token": csrf }, data: { language: "lt" } });
  await page.reload();
  await expect(page.getByTestId("search-toggle")).toHaveAccessibleName("Paieška internete šiame pokalbyje");
  await expect(page.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Paieškos internete parinktys" }).click();
  await expect(page.getByRole("menuitemradio", { name: /^Įjungta/ })).toHaveAttribute("aria-checked", "true");
  await page.keyboard.press("Escape");
  await context.close();
});

test("Auto: the model decides, the decision is shown, and 'Search the web' is one click away", async ({ browser }) => {
  const context = await signUp(browser);
  const page = await context.newPage();
  await page.goto("/");
  await page.getByRole("button", { name: "Web search options" }).click();
  await page.getByRole("menuitemradio", { name: /^Auto/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Turn on web search" }).click();
  await expect(page.getByTestId("search-toggle")).toHaveAccessibleName("Auto web search in this conversation");

  await send(page, "What's the latest news about the Vilnius festival?");
  const steps = lastAnswer(page).getByTestId("web-steps");
  await expect(steps).toContainText(/Used \d sources/);
  await steps.getByRole("button").first().click();
  await expect(steps).toContainText("Searching the web: current information");

  await send(page, "Write a haiku about autumn");
  await expect(lastAnswer(page).getByTestId("web-steps")).toContainText("No web search needed: general question");
  await lastAnswer(page).getByRole("button", { name: "Search the web" }).click();
  await expect(lastAnswer(page).getByTestId("web-steps")).toContainText(/Used \d sources/, { timeout: 20_000 });
  // The conversation is still on Auto.
  await expect(page.getByTestId("search-control")).toHaveAttribute("data-mode", "auto");
  await context.close();
});
