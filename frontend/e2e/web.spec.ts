import { expect, test, type Page } from "@playwright/test";

// Mock web server started by playwright.config.ts (pages + SearXNG JSON API).
const WEB = "http://127.0.0.1:18090";
const composer = (page: Page) => page.getByLabel(/^(Message|Žinutė)$/);

test.describe.configure({ mode: "serial" });

test.afterAll(async ({ browser }) => {
  // Leave web access off for the other test files.
  const context = await browser.newContext({ storageState: "test-results/admin-state.json" });
  const page = await context.newPage();
  await page.goto("/");
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")?.value ?? "";
  await page.request.patch("/api/admin/web", { headers: { "X-CSRF-Token": csrf }, data: { settings: { enabled: false } } });
  await context.close();
});

test("without web access: no search toggle, links are not read, chat works", async ({ page }) => {
  await page.goto("/");
  await expect(composer(page)).toBeVisible();
  await expect(page.getByTestId("search-toggle")).toHaveCount(0);
  await composer(page).fill(`Offline check ${WEB}/article`);
  await expect(page.getByText("Links are not read because web access is turned off on this server.")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.getByText(`You said: Offline check ${WEB}/article`)).toBeVisible();
  await expect(page.getByTestId("link-status")).toHaveCount(0);
});

test("admin configures the search engine, then links are read and search cites sources", async ({ page }) => {
  // --- configure as administrator
  await page.goto("/settings/web");
  await page.getByRole("switch", { name: "Allow web access" }).click();
  await expect(page.getByText("Web settings saved").first()).toBeVisible();
  await page.getByLabel("Engine", { exact: true }).selectOption("searxng");
  await page.getByLabel("SearXNG address").fill(WEB);
  await page.getByRole("button", { name: "Test search" }).click();
  await expect(page.getByTestId("engine-status")).toContainText("Working: 5 results");
  await page.getByRole("button", { name: "Save engine" }).click();
  // The mock site runs on this machine: allow it explicitly (private addresses are blocked by default).
  await page.getByLabel("Internal hosts that may be read").fill("127.0.0.1");
  await page.getByLabel("User agent").click();
  await expect(page.getByText("Web settings saved").first()).toBeVisible();

  // --- paste a URL
  await page.goto("/");
  await composer(page).fill(`Summarize ${WEB}/article please`);
  const chip = page.getByRole("list", { name: "Links in this message" }).getByRole("button");
  await expect(chip).toHaveText(/127\.0\.0\.1:18090\/article/);
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Enter");
  // The answer quotes the page's actual content, with a citation to it.
  await expect(page.getByText(/Aurora Borealis Festival comes to Vilnius/).first()).toBeVisible();
  const cite = page.getByRole("link", { name: "Source 1: Aurora Borealis Festival comes to Vilnius" });
  await expect(cite).toBeVisible();
  await expect(cite).toHaveAttribute("target", "_blank");
  await expect(page.getByTestId("link-status").getByRole("button")).toContainText("127.0.0.1");
  await expect(page.getByTestId("web-sources")).toContainText("Aurora Borealis Festival comes to Vilnius");

  // --- a link that can't be read is reported, and the model is told
  await composer(page).fill(`And ${WEB}/status/404 ?`);
  await page.keyboard.press("Enter");
  await expect(page.getByText("Source [2] could not be read.").or(page.getByText(/could not be read\./)).first()).toBeVisible();
  await expect(page.getByRole("button", { name: /Could not read 127\.0\.0\.1: The page was not found\./ })).toBeVisible();

  // --- search with the toggle (privacy notice first)
  await page.getByTestId("search-toggle").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("What web search sends");
  await dialog.getByRole("button", { name: "Turn on web search" }).click();
  await expect(page.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "true");
  await composer(page).fill("What happens in Vilnius in March?");
  await page.keyboard.press("Enter");
  const answer = page.locator("li").filter({ has: page.getByTestId("web-steps") }).last();
  await expect(answer.getByRole("button", { name: "Show web activity" })).toContainText(/Used \d sources/);
  await answer.getByRole("button", { name: "Show web activity" }).click();
  await expect(answer.getByText(/Searching for: /)).toBeVisible();
  await expect(answer.getByText("Found 4 results")).toBeVisible();
  await expect(answer.getByRole("link", { name: /^Source 1: / })).toBeVisible();
  const sources = answer.getByTestId("web-sources").getByRole("link");
  await expect(sources.first()).toHaveAttribute("rel", /noreferrer/);
  expect(await sources.count()).toBeGreaterThanOrEqual(3);
  // Search turns itself off after the message.
  await expect(page.getByTestId("search-toggle")).toHaveAttribute("aria-pressed", "false");

  // --- the reloaded conversation shows the same steps and sources
  await page.reload();
  await expect(page.getByRole("link", { name: /^Source 1: / }).last()).toBeVisible();
  await expect(page.getByTestId("web-steps").last()).toContainText(/Used \d sources/);
});

test("Lithuanian: web UI strings switch instantly, stop works, narrow layout holds", async ({ page }) => {
  await page.goto("/settings/appearance");
  await page.getByRole("combobox", { name: "Language" }).selectOption("lt");
  await expect(page.locator("html")).toHaveAttribute("lang", "lt");
  await page.goto("/");
  await expect(page.getByTestId("search-toggle")).toHaveAccessibleName("Ieškoti internete šiai žinutei");

  await page.getByTestId("search-toggle").click();
  await composer(page).fill("festivalis [slow]");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("web-steps").last()).toBeVisible();
  await expect(page.getByText(/word\d/).first()).toBeVisible(); // the answer is streaming
  await page.getByRole("button", { name: "Sustabdyti generavimą" }).click();
  await expect(page.getByText("Sustabdyta").first()).toBeVisible();
  await expect(page.getByText("Šaltiniai").first()).toBeVisible();
  // The conversation is usable again.
  await composer(page).fill("po sustabdymo");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: po sustabdymo")).toBeVisible();

  // Narrow phone width: composer toggle, chips, steps, sources and settings must not overflow.
  await page.setViewportSize({ width: 360, height: 740 });
  await composer(page).fill(`Skaityk ${WEB}/article ir ${WEB}/docs`);
  await page.getByTestId("search-toggle").click();
  for (const check of ["chat", "/settings/web"]) {
    if (check !== "chat") {
      await page.goto(check);
      await page.waitForLoadState("networkidle");
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${check}`).toBeLessThanOrEqual(0);
  }
  await expect(page.getByRole("heading", { name: "Prieiga prie interneto visiems" })).toBeVisible();

  await page.goto("/settings/appearance");
  await page.getByRole("combobox", { name: "Kalba" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});
