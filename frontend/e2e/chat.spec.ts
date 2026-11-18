import { expect, test, type Page } from "@playwright/test";

const composer = (page: Page) => page.getByLabel("Message", { exact: true });

async function newChat(page: Page) {
  await page.goto("/");
  await expect(composer(page)).toBeVisible();
  // The seeded llama-server connection is discovered automatically.
  await expect(page.getByRole("button", { name: /^Model: qwen2\.5/ })).toBeVisible();
}

test("offline: the UI loads with all external network blocked and makes no external requests", async ({ page, context }) => {
  const external: string[] = [];
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
      external.push(url.href);
      return route.abort();
    }
    return route.continue();
  });
  await newChat(page);
  await composer(page).fill("[markdown] offline check");
  await page.keyboard.press("Enter");
  await expect(page.locator(".katex").first()).toBeVisible();
  await page.goto("/settings");
  await page.goto("/models");
  expect(external).toEqual([]);
});

test("send, stream, persist, title and restore after reload", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("Hello from the end-to-end test");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/c\/[0-9a-f]+$/);
  await expect(page.getByText("You said: Hello from the end-to-end test")).toBeVisible();
  // Model-generated title replaces the fallback in the sidebar.
  await expect(page.getByRole("link", { name: "Mock Conversation Title" }).first()).toBeVisible();
  await expect(page.getByText(/42\.3 tok\/s/).first()).toBeVisible();

  await page.reload();
  await expect(page.getByText("You said: Hello from the end-to-end test")).toBeVisible();
  await expect(page.getByText("Hello from the end-to-end test", { exact: true })).toBeVisible();
});

test("the title updates live in the sidebar, top bar and tab, also with a reasoning model", async ({ page, request }) => {
  // Like Qwen3: the model thinks before writing a title (the bug left "New conversation" until a reload).
  await request.post("http://127.0.0.1:18080/__mock/think-titles", { data: { enabled: true } });
  try {
    await newChat(page);
    await page.evaluate(() => ((window as unknown as { __sameDocument: boolean }).__sameDocument = true));
    await composer(page).fill("[slow] Testas-testas?");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/c\/[0-9a-f]+$/);
    // This conversation's entry in the sidebar (other tests leave chats with the same titles).
    const entry = page.locator(`a[href="${new URL(page.url()).pathname}"]`).getByTestId("conversation-entry-title");
    // While the answer streams, the fallback title from the first message is already shown.
    await expect(entry).toHaveText("[slow] Testas-testas?");
    await expect(page.getByTestId("conversation-title")).toHaveText("[slow] Testas-testas?");
    // Then the model's title replaces it everywhere.
    await expect(entry).toHaveText("Mock Conversation Title", { timeout: 15_000 });
    await expect(page.getByTestId("conversation-title")).toHaveText("Mock Conversation Title");
    await expect(page).toHaveTitle("Mock Conversation Title · Chimera");
    // No reload happened.
    expect(await page.evaluate(() => (window as unknown as { __sameDocument?: boolean }).__sameDocument)).toBe(true);
  } finally {
    await request.post("http://127.0.0.1:18080/__mock/think-titles", { data: { enabled: false } });
  }
});

test("stop generation keeps partial output and the chat stays usable", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("go [slow]");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/word1 word2/)).toBeVisible();
  await page.getByRole("button", { name: "Stop generating" }).click();
  await expect(page.getByText("Stopped")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send message" })).toBeVisible();
  await composer(page).fill("next");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: next")).toBeVisible();
});

test("upstream errors are shown, and editing a message resubmits it", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("please fail [error]");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert").filter({ hasText: "the mock server exploded" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  const userMsg = page.getByRole("main").getByRole("list").getByText("please fail [error]", { exact: true });
  await userMsg.hover();
  await page.getByRole("button", { name: "Edit" }).first().click();
  const editor = page.getByLabel("Edit message");
  await editor.fill("please succeed");
  await page.getByRole("button", { name: "Save and resubmit" }).click();
  await expect(page.getByText("You said: please succeed")).toBeVisible();
  await expect(page.getByText("the mock server exploded")).toHaveCount(0);
});

test("regenerate replaces the last answer", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("regenerate me");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: regenerate me")).toBeVisible();
  await page.getByRole("button", { name: "Regenerate" }).last().click();
  await expect(page.getByText("You said: regenerate me")).toHaveCount(1);
});

test("code blocks render with highlighting and a copy button", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await newChat(page);
  await composer(page).fill("[markdown] code please");
  await page.keyboard.press("Enter");
  const copy = page.getByRole("button", { name: "Copy code" });
  await expect(copy).toBeVisible();
  await expect(page.locator("pre code.hljs")).toBeVisible();
  await expect(page.locator("table")).toBeVisible();
  await copy.click();
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
});

test("conversation rename, pin and delete from the sidebar", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("manage me");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: manage me")).toBeVisible();
  const item = page.getByRole("link", { name: "Mock Conversation Title" }).first();
  await item.hover();
  await page.getByRole("button", { name: "Options for “Mock Conversation Title”" }).first().click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  const input = page.getByRole("textbox", { name: "Conversation title" });
  await input.fill("Renamed chat");
  await input.press("Enter");
  await expect(page.getByRole("link", { name: "Renamed chat" })).toBeVisible();

  await page.getByRole("link", { name: "Renamed chat" }).hover();
  await page.getByRole("button", { name: "Options for “Renamed chat”" }).click();
  await page.getByRole("menuitem", { name: "Pin" }).click();
  await expect(page.getByRole("heading", { name: "Pinned" })).toBeVisible();

  await page.getByRole("link", { name: "Renamed chat" }).hover();
  await page.getByRole("button", { name: "Options for “Renamed chat”" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("link", { name: "Renamed chat" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/$/);
});

test("search finds conversations by content", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("unique pelican keyword");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: unique pelican keyword")).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Search conversations" }).fill("pelican");
  await expect(page.getByRole("option").first()).toContainText("pelican");
});

test("waiting indicator: orbits smoothly, calms down with reduced motion, and the answer doesn't jump", async ({ page }) => {
  await newChat(page);
  await composer(page).fill("[waitfirst] short answer");
  await page.keyboard.press("Enter");
  const waiting = page.getByRole("status", { name: "Waiting for the model" });
  await expect(waiting).toBeVisible();
  const indicator = waiting.getByTestId("thinking-indicator");
  await expect(indicator).toHaveAttribute("aria-hidden", "true");
  const orbit = indicator.locator(".thinking-orbit");
  // Compositor-friendly: only transform animates.
  expect(await orbit.evaluate((el) => getComputedStyle(el).animationName)).toBe("thinking-orbit");
  expect(await orbit.evaluate((el) => getComputedStyle(el).willChange)).toBe("transform");
  const before = await waiting.boundingBox();

  // The first line of the answer takes the indicator's place: same top, same line height.
  const paragraph = page.getByText("You said: [waitfirst] short answer");
  await expect(paragraph).toBeVisible({ timeout: 10_000 });
  const after = await paragraph.boundingBox();
  expect(Math.abs((after?.y ?? 0) - (before?.y ?? 0))).toBeLessThanOrEqual(1);
  expect(Math.abs((after?.height ?? 0) - (before?.height ?? 0))).toBeLessThanOrEqual(1);

  // Reduced motion (system setting): the arc stops and the core breathes slowly instead.
  await page.emulateMedia({ reducedMotion: "reduce" });
  const probe = await page.evaluate(() => {
    const el = document.createElement("span");
    el.className = "thinking-indicator";
    el.innerHTML = '<span class="thinking-orbit"></span><span class="thinking-core"></span>';
    document.body.append(el);
    const orbitStyle = getComputedStyle(el.children[0]);
    const coreStyle = getComputedStyle(el.children[1]);
    const out = { orbit: orbitStyle.animationName, core: coreStyle.animationName, coreDuration: coreStyle.animationDuration };
    el.remove();
    return out;
  });
  expect(probe).toEqual({ orbit: "none", core: "thinking-pulse", coreDuration: "2.4s" });
});
