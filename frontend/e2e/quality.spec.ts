import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

// Accessibility, narrow layouts, long conversations and the lazily loaded
// bundle. Each test signs up its own account, so switching its language or
// filling it with messages never affects the administrator the other tests use.

let accounts = 0;

async function signUp(browser: Browser, language: "en" | "lt", options: Parameters<Browser["newContext"]>[0] = {}) {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] }, ...options });
  const name = `quality${Date.now().toString(36)}${accounts++}`;
  await context.request.get("/api/auth/status");
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")!.value;
  const resp = await context.request.post("/api/auth/register", {
    headers: { "X-CSRF-Token": csrf },
    data: { email: `${name}@example.com`, username: name, password: "amber meadow lantern 7731", password_confirm: "amber meadow lantern 7731", language },
  });
  expect(resp.ok(), await resp.text()).toBeTruthy();
  return { context, csrf: (await context.cookies()).find((c) => c.name === "chimera_csrf")!.value };
}

/** One chat with a Markdown answer (code, table, math), so the chat view is audited with real content. */
async function chatWithAnswer(page: Page, prompt: string) {
  await page.goto("/");
  const composer = page.getByRole("textbox", { name: /^(Message|Žinutė)$/ });
  await expect(page.getByRole("button", { name: /^(Model|Modelis): / })).toBeVisible();
  await composer.fill(prompt);
  await page.keyboard.press("Enter");
  await expect(page.locator(".katex").first()).toBeVisible();
  await expect(page.getByTestId("message-meta").first()).toBeVisible();
  return page.url();
}

const PAGES = ["/", "/models", "/knowledge", "/prompts", "/settings/account", "/settings/appearance", "/settings/chat", "/settings/data"];

async function axe(page: Page) {
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

for (const language of ["en", "lt"] as const) {
  test(`axe finds no WCAG 2.2 AA violations on the main pages (${language}, light and dark)`, async ({ browser }) => {
    test.setTimeout(120_000);
    let ctx: BrowserContext | undefined;
    try {
      const { context } = await signUp(browser, language);
      ctx = context;
      const page = await context.newPage();
      const chat = await chatWithAnswer(page, "[markdown] accessibility check");
      for (const scheme of ["light", "dark"] as const) {
        await page.emulateMedia({ colorScheme: scheme });
        for (const path of [new URL(chat).pathname, ...PAGES]) {
          await page.goto(path);
          await page.waitForLoadState("networkidle");
          expect(await axe(page), `${path} (${language}, ${scheme})`).toEqual([]);
        }
      }
      // Signed out: the sign-in page.
      await context.clearCookies();
      await page.goto("/login");
      await expect(page.getByRole("button", { name: /^(Sign in|Prisijungti)$/ })).toBeVisible();
      expect(await axe(page), `/login (${language})`).toEqual([]);
    } finally {
      await ctx?.close();
    }
  });
}

test("at 360 px nothing overflows sideways and answer details shorten instead of being cut", async ({ browser }) => {
  test.setTimeout(90_000);
  const { context } = await signUp(browser, "lt", { viewport: { width: 360, height: 760 }, isMobile: true, hasTouch: true });
  try {
    const page = await context.newPage();
    const chat = await chatWithAnswer(page, "[markdown] narrow check");
    for (const path of [new URL(chat).pathname, ...PAGES, "/settings/web", "/settings/users", "/settings/about"]) {
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
    }
    await page.goto(chat);
    const meta = page.getByTestId("message-meta").first();
    await expect(meta).toBeVisible();
    // One line, ending in an ellipsis if it must; never wider than the screen.
    const box = await meta.boundingBox();
    expect(box!.x + box!.width).toBeLessThanOrEqual(360);
    expect(await meta.evaluate((el) => getComputedStyle(el).textOverflow)).toBe("ellipsis");
    // The sidebar opens as a drawer and its conversation list is a landmark.
    await page.getByRole("button", { name: "Atverti šoninę juostą" }).click();
    await expect(page.getByRole("navigation", { name: "Pokalbiai" })).toBeVisible();
  } finally {
    await context.close();
  }
});

test("a 300-message conversation opens with the latest messages and loads earlier ones in place", async ({ browser }) => {
  test.setTimeout(90_000);
  const { context, csrf } = await signUp(browser, "en");
  try {
    const messages = Array.from({ length: 300 }, (_, i) => ({
      role: i % 2 ? "assistant" : "user",
      content: i % 2 ? `Answer ${i}: **bold** text and a list\n\n- one\n- two` : `Question ${i}?`,
      position: i,
    }));
    const resp = await context.request.post("/api/data/import", {
      headers: { "X-CSRF-Token": csrf },
      multipart: { file: { name: "export.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ format: "chimera-export", conversations: [{ id: "long-e2e", title: "Long one", messages }] })) } },
    });
    expect(resp.ok(), await resp.text()).toBeTruthy();
    const page = await context.newPage();
    await page.goto("/c/long-e2e");
    const list = page.getByRole("list", { name: "Messages" });
    const rows = list.locator(":scope > li");
    await expect(list.getByText("Answer 299:")).toBeVisible();
    await expect(rows).toHaveCount(60);
    await expect(list.getByText("Question 0?")).not.toBeAttached();
    // Showing earlier messages keeps what the reader is looking at in place.
    // (Positions are read in the page: Playwright's own scrolling would itself load more.)
    const scroller = (y: number) => list.evaluate((el, top) => el.closest(".overflow-y-auto")!.scrollTo({ top }), y);
    await scroller(2000); // reading somewhere in the middle, away from the top
    const anchor = list.locator(":scope > li").nth(30);
    const top = () => anchor.evaluate((el) => el.getBoundingClientRect().top);
    const text = await anchor.textContent();
    const before = await top();
    await page.getByRole("button", { name: "Show 240 earlier messages" }).dispatchEvent("click");
    await expect(rows).toHaveCount(140);
    const same = list.locator(":scope > li").filter({ hasText: text! }).first();
    expect(Math.abs((await same.evaluate((el) => el.getBoundingClientRect().top)) - before)).toBeLessThanOrEqual(2);
    await expect(page.getByRole("button", { name: "Show 160 earlier messages" })).toBeAttached();
    // Scrolling to the top loads the rest on its own.
    await expect
      .poll(async () => {
        await list.evaluate((el) => el.closest(".overflow-y-auto")!.scrollTo({ top: 0 }));
        return rows.count();
      })
      .toBe(300);
    await expect(rows).toHaveCount(300);
    await expect(list.getByText("Question 0?")).toBeAttached();
  } finally {
    await context.close();
  }
});

test("the sign-in page downloads neither the chat, the Markdown renderer nor the other pages", async ({ browser }) => {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  try {
    const page = await context.newPage();
    const scripts: string[] = [];
    page.on("request", (r) => r.resourceType() === "script" && scripts.push(new URL(r.url()).pathname));
    await page.goto("/login");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    await page.waitForLoadState("networkidle");
    expect(scripts.some((s) => /markdown/i.test(s)), scripts.join(", ")).toBe(false);
    expect(scripts.some((s) => /ChatPage|SettingsPage|ModelsPage/.test(s)), scripts.join(", ")).toBe(false);
  } finally {
    await context.close();
  }
});
