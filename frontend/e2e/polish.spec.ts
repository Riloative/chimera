import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// 1.2 polish: the stop button, the model picker by keyboard, find in long
// conversations, failed loads, touch screens, and axe on the new menus.

const composer = (page: Page) => page.getByLabel("Message", { exact: true });

async function newChat(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /^Model: / })).toBeVisible();
}

const box = async (page: Page, testId: string) => (await page.getByTestId(testId).boundingBox())!;

test("stop button: same place and size as Send, named, no motion, visible focus", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await newChat(page);
  // Inside a conversation (the start page moves the composer once a chat begins).
  await composer(page).fill("hello");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: hello")).toBeVisible();
  await composer(page).fill("[slow] a long answer to stop");
  const send = await box(page, "send-button");
  const sendColor = await page.getByTestId("send-button").evaluate((el) => getComputedStyle(el).backgroundColor);
  await page.keyboard.press("Enter");
  const stop = page.getByRole("button", { name: "Stop generating" });
  await expect(stop).toBeVisible();
  const s = await box(page, "stop-button");
  // Nothing shifts when they swap (WCAG 2.2 target size: 32×32 ≥ 24×24).
  expect(Math.abs(s.x - send.x) + Math.abs(s.y - send.y) + Math.abs(s.width - send.width) + Math.abs(s.height - send.height)).toBeLessThan(1);
  expect(s.width).toBeGreaterThanOrEqual(24);
  expect(await stop.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(sendColor);
  // No animation in or on the button (also not with reduced motion off: it simply has none).
  expect(await stop.evaluate((el) => [el, ...el.querySelectorAll("*")].flatMap((n) => n.getAnimations()).length)).toBe(0);
  // Keyboard focus is clearly visible: a solid 2 px ring.
  await page.keyboard.press("Shift");
  await stop.focus();
  const ring = await stop.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { style: cs.outlineStyle, width: cs.outlineWidth, color: cs.outlineColor };
  });
  expect(ring.style).toBe("solid");
  expect(ring.width).toBe("2px");
  expect(ring.color).not.toMatch(/rgba\(.*, 0(\.\d+)?\)$/); // not transparent
  await stop.click();
  await expect(page.getByTestId("send-button")).toBeVisible();
  const back = await box(page, "send-button");
  expect(Math.abs(back.x - send.x) + Math.abs(back.y - send.y)).toBeLessThan(1);
});

test("model picker works with the keyboard and gives focus back", async ({ page }) => {
  await newChat(page);
  const trigger = page.getByRole("button", { name: /^Model: / });
  const before = await trigger.getAttribute("aria-label");
  await trigger.focus();
  await page.keyboard.press("Enter");
  const search = page.getByRole("combobox", { name: "Search models" });
  await expect(search).toBeFocused();
  const first = await search.getAttribute("aria-activedescendant");
  await page.keyboard.press("ArrowDown");
  const second = await search.getAttribute("aria-activedescendant");
  expect(second).not.toBe(first);
  expect(await page.evaluate((id) => document.getElementById(id!)?.getAttribute("role"), second)).toBe("option");
  await page.keyboard.press("Enter");
  await expect(search).toHaveCount(0);
  await expect(trigger).not.toHaveAttribute("aria-label", before!);
  await expect(trigger).toBeFocused();
  // Escape closes and returns focus too.
  await page.keyboard.press("Enter");
  await expect(page.getByRole("combobox", { name: "Search models" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  // Put the first model back for the other tests.
  await page.keyboard.press("Enter");
  await page.keyboard.press("Home");
  await page.keyboard.press("Enter");
});

test("find in page (Ctrl+F) can reach the first message of a long conversation", async ({ page, context }) => {
  const csrf = (await context.cookies()).find((c) => c.name === "chimera_csrf")!.value;
  const messages = Array.from({ length: 200 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: i % 2 ? `Answer ${i}` : `Question ${i}?`, position: i }));
  const id = `find-${Date.now().toString(36)}`;
  const resp = await page.request.post("/api/data/import", {
    headers: { "X-CSRF-Token": csrf },
    multipart: { file: { name: "e.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ format: "chimera-export", conversations: [{ id, title: "Find me", messages }] })) } },
  });
  expect(resp.ok()).toBeTruthy();
  await page.goto(`/c/${id}`);
  const rows = page.getByRole("list", { name: "Messages" }).locator(":scope > li");
  await expect(rows).toHaveCount(60);
  await page.keyboard.press("Control+f");
  await expect(rows).toHaveCount(200);
  await expect(page.getByText("Question 0?")).toBeAttached();
});

test("a failed load says so and can be retried, instead of spinning forever", async ({ page }) => {
  let fail = true;
  await page.route("**/api/prompts", (route) =>
    fail ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { code: "unavailable", message: "Down for a moment" } }) }) : route.continue(),
  );
  await page.goto("/prompts");
  const alert = page.getByRole("alert").filter({ hasText: "This couldn't be loaded." });
  await expect(alert).toBeVisible();
  fail = false;
  await alert.getByRole("button", { name: "Try again" }).click();
  await expect(alert).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Prompts" })).toBeVisible();
});

test.describe("touch screen", () => {
  test.use({ viewport: { width: 360, height: 740 }, isMobile: true, hasTouch: true });
  test("conversation options are visible without hover", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Open sidebar" }).click();
    const nav = page.getByRole("navigation", { name: "Conversations" });
    const options = nav.getByRole("button", { name: /^Options for/ });
    await expect(options.first()).toBeVisible();
    expect(await options.first().evaluate((el) => getComputedStyle(el).opacity)).toBe("1");
  });
});

test("axe: no violations with the model picker open (light and dark)", async ({ page }) => {
  for (const scheme of ["light", "dark"] as const) {
    // Reduced motion: measure the settled colours, not a frame of the pop-in.
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await newChat(page);
    const violations = async () =>
      (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze()).violations.map(
        (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
      );
    await page.getByRole("button", { name: /^Model: / }).click();
    await expect(page.getByRole("listbox")).toBeVisible();
    expect(await violations(), `model picker (${scheme})`).toEqual([]);
    await page.keyboard.press("Escape");
  }
});
