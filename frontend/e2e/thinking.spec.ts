import AxeBuilder from "@axe-core/playwright";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Thinking per conversation (1.3) against the mock Anthropic API (which
// enforces each model family's rules) and the mock llama-server; and the
// context indicator across conversations of different lengths.

const CLAUDE = "http://127.0.0.1:18081";
const LLAMA = "http://127.0.0.1:18080";
const composer = (page: Page) => page.getByRole("textbox", { name: /^(Message|Žinutė)$/ });
const control = (page: Page) => page.getByTestId("thinking-control");

async function csrf(page: Page) {
  return (await page.context().cookies()).find((c) => c.name === "chimera_csrf")!.value;
}

async function claudeRequests(request: APIRequestContext, clear = false) {
  const resp = await request.post(`${CLAUDE}/__mock/config`, { data: { clear } });
  return ((await resp.json()) as { requests: Record<string, unknown>[] }).requests.filter(
    (b) => !JSON.stringify(b.messages).includes("Write a short, specific title"),
  );
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("send-button")).toBeVisible({ timeout: 20_000 });
}

async function pickModel(page: Page, name: RegExp) {
  await page.getByRole("button", { name: /^(Model|Modelis): / }).click();
  await page.getByRole("option", { name }).click();
  await expect(page.getByRole("combobox", { name: /^(Search models|Ieškoti modelių)$/ })).toHaveCount(0);
}

async function chooseLevel(page: Page, level: RegExp) {
  await control(page).click();
  await page.getByRole("menuitemradio", { name: level }).click();
}

test.describe.configure({ mode: "serial" });

let providerId = "";

test.beforeAll(async ({ browser }) => {
  const context = await browser.newContext({ storageState: "test-results/admin-state.json" });
  const page = await context.newPage();
  await page.goto("/");
  const headers = { "X-CSRF-Token": await csrf(page) };
  const resp = await page.request.post("/api/providers", {
    headers,
    data: { name: "Claude (mock)", kind: "anthropic", base_url: CLAUDE, api_key: "sk-ant-e2e" },
  });
  expect(resp.ok(), await resp.text()).toBeTruthy();
  providerId = (await resp.json()).id;
  expect((await page.request.post(`/api/providers/${providerId}/test`, { headers })).ok()).toBeTruthy();
  await context.close();
});

test.afterAll(async ({ browser }) => {
  const context = await browser.newContext({ storageState: "test-results/admin-state.json" });
  const page = await context.newPage();
  await page.goto("/");
  const headers = { "X-CSRF-Token": await csrf(page) };
  await page.request.delete(`/api/providers/${providerId}`, { headers });
  await page.request.post(`${LLAMA}/__mock/config`, { data: { chat_template: "{{ messages }}" } });
  await page.request.patch("/api/settings", { headers, data: { thinking_default: "default" } });
  await context.close();
});

test("no control for a model without thinking; levels for one that has them, sent per level, kept after reload", async ({ page, request }) => {
  await page.goto("/");
  await pickModel(page, /qwen2\.5-7b/);
  await expect(control(page)).toHaveCount(0); // the mock llama-server's template has no thinking switch

  await pickModel(page, /Claude Sonnet 4\.6/);
  await expect(control(page)).toHaveAccessibleName("Thinking: the model's default");
  const box = (await control(page).boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(24);
  expect(box.height).toBeGreaterThanOrEqual(24);
  // It sits right before the model picker.
  expect(await control(page).evaluate((el) => el.nextElementSibling?.getAttribute("aria-label"))).toMatch(/^Model: /);

  await chooseLevel(page, /^High/);
  await expect(control(page)).toBeFocused(); // focus back on the trigger
  await expect(control(page)).toHaveAccessibleName("High thinking");
  await claudeRequests(request, true);
  await send(page, "hello claude");
  await expect(page).toHaveURL(/\/c\/[0-9a-f]+$/);
  let sent = await claudeRequests(request);
  expect(sent.at(-1)).toMatchObject({ thinking: { type: "adaptive", display: "summarized" }, output_config: { effort: "high" } });
  // The thinking summary is shown as reasoning.
  await page.getByRole("button", { name: "Thought process" }).last().click();
  await expect(page.getByText("Thinking at high.")).toBeVisible();

  // Each level, as Anthropic expects it for this model.
  const expected: [RegExp, Record<string, unknown> | null][] = [
    [/^Default/, null],
    [/^Off/, { type: "disabled" }],
    [/^Low/, { type: "adaptive", display: "summarized" }],
    [/^Max/, { type: "adaptive", display: "summarized" }],
  ];
  for (const [level, thinking] of expected) {
    await chooseLevel(page, level);
    await send(page, `level ${level.source}`);
    sent = await claudeRequests(request);
    const body = sent.at(-1)!;
    if (thinking) expect(body.thinking).toEqual(thinking);
    else expect(body).not.toHaveProperty("thinking");
  }
  expect(sent.at(-1)!.output_config).toEqual({ effort: "max" });

  // Stored on the server: the same after a reload.
  await page.reload();
  await expect(control(page)).toHaveAccessibleName("Max thinking");

  // Another model in the same conversation: Opus 5.5 can't switch thinking off.
  await chooseLevel(page, /^Off/);
  await pickModel(page, /Claude Opus 5\.5/);
  await expect(control(page)).toHaveAccessibleName("Low thinking");
  await control(page).click();
  await expect(page.getByTestId("thinking-clamped")).toHaveText("Off isn't available for this model, so low is used.");
  await page.keyboard.press("Escape");
  await send(page, "on opus");
  expect((await claudeRequests(request)).at(-1)).toMatchObject({ model: "claude-opus-5-5", output_config: { effort: "low" } });
  await expect(page.getByTestId("thinking-note").last()).toHaveText("· low thinking");
  // Back to the first model: the conversation's level was kept.
  await pickModel(page, /Claude Sonnet 4\.6/);
  await expect(control(page)).toHaveAccessibleName("Off: answers without thinking");
});

test("the menu is accessible in light and dark", async ({ page }) => {
  await page.goto("/");
  await pickModel(page, /Claude Sonnet 4\.6/);
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme, reducedMotion: "reduce" });
    await control(page).click();
    await expect(page.getByRole("menu")).toBeVisible();
    const found = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(found.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`), scheme).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(control(page)).toBeFocused();
  }
});

test("an on/off model gets one switch; the default for new conversations applies", async ({ page, request }) => {
  await request.post(`${LLAMA}/__mock/config`, { data: { chat_template: "{% if enable_thinking %}<think>{% endif %}", clear: true } });
  await page.goto("/");
  await pickModel(page, /qwen2\.5-7b/);
  const toggle = page.getByRole("button", { name: "Thinking", exact: true });
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  await send(page, "no thinking please");
  await expect(page.getByText("You said: no thinking please")).toBeVisible();
  const resp = await request.post(`${LLAMA}/__mock/config`, { data: {} });
  const bodies = ((await resp.json()) as { requests: Record<string, unknown>[] }).requests;
  const answer = bodies.filter((b) => !JSON.stringify(b.messages).includes("Write a short, specific title")).at(-1)!;
  expect(answer.chat_template_kwargs).toEqual({ enable_thinking: false });

  // Settings → Chat: the default for new conversations.
  await page.goto("/settings/chat");
  await page.getByLabel("Thinking in new conversations").selectOption("high");
  await expect.poll(async () => (await (await page.request.get("/api/settings")).json()).thinking_default).toBe("high");
  await page.goto("/");
  await pickModel(page, /Claude Sonnet 4\.6/);
  await expect(control(page)).toHaveAccessibleName("High thinking");
});

test("Lithuanian", async ({ page }) => {
  const headers = { "X-CSRF-Token": "" };
  await page.goto("/");
  headers["X-CSRF-Token"] = await csrf(page);
  await page.request.patch("/api/account", { headers, data: { language: "lt" } });
  try {
    await page.reload();
    await pickModel(page, /Claude Sonnet 4\.6/);
    await expect(control(page)).toHaveAccessibleName("Išsamus mąstymas"); // the default set above: High
    await control(page).click();
    await expect(page.getByRole("menu")).toContainText("Mąstymas šiame pokalbyje");
    await expect(page.getByRole("menuitemradio", { name: /^Trumpas/ })).toBeVisible();
    await page.keyboard.press("Escape");
  } finally {
    await page.request.patch("/api/account", { headers, data: { language: "en" } });
  }
});

test("phone: the control is an icon next to the model picker and Send stays on screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/");
  await pickModel(page, /Claude Sonnet 4\.6/);
  const send = (await page.getByTestId("send-button").boundingBox())!;
  expect(send.x + send.width).toBeLessThanOrEqual(360);
  await expect(control(page)).toHaveAccessibleName(/thinking/i);
});

test("context indicator: a short and a long conversation each show their own numbers, also after a reload", async ({ page }) => {
  await page.goto("/");
  const headers = { "X-CSRF-Token": await csrf(page) };
  const stamp = Date.now().toString(36);
  const conv = (id: string, n: number) => ({
    id,
    title: id,
    provider_id: null,
    messages: Array.from({ length: n }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: `${i % 2 ? "Answer" : "Question"} ${i} ${"word ".repeat(30)}`, position: i })),
  });
  const short = `short-${stamp}`;
  const long = `long-${stamp}`;
  const resp = await page.request.post("/api/data/import", {
    headers,
    multipart: { file: { name: "e.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify({ format: "chimera-export", conversations: [conv(short, 2), conv(long, 40)] })) } },
  });
  expect(resp.ok()).toBeTruthy();
  // Imported conversations have no connection: give both the same model.
  const seeded = (await (await page.request.get("/api/providers")).json()).find((p: { kind: string }) => p.kind === "llamacpp");
  for (const id of [short, long]) {
    await page.request.patch(`/api/conversations/${id}`, { headers, data: { provider_id: seeded.id, model: "qwen2.5-7b-instruct-q4_k_m.gguf" } });
  }
  const api = async (id: string) => (await (await page.request.get(`/api/conversations/${id}/context`)).json()).used as number;
  const [shortUsed, longUsed] = [await api(short), await api(long)];
  expect(longUsed).toBeGreaterThan(shortUsed * 5);

  const indicator = page.getByTestId("context-indicator");
  const shown = async () => {
    const m = ((await indicator.getAttribute("aria-label")) ?? "").match(/: ([\d.,]+)\s*(K?)\s*\//);
    return m ? parseFloat(m[1].replace(",", "")) * (m[2] ? 1000 : 1) : -1;
  };
  const near = (value: number, expected: number) => Math.abs(value - expected) <= Math.max(60, expected * 0.06);

  await page.goto(`/c/${short}`);
  await expect(indicator).toBeVisible();
  expect(near(await shown(), shortUsed)).toBeTruthy();
  // Switch by clicking in the sidebar, then back: never the other conversation's numbers.
  await page.getByRole("navigation", { name: "Conversations" }).getByRole("link", { name: long }).click();
  await expect(page).toHaveURL(new RegExp(`/c/${long}$`));
  await expect.poll(shown).toBeGreaterThan(shortUsed * 3);
  expect(near(await shown(), longUsed)).toBeTruthy();
  await page.getByRole("navigation", { name: "Conversations" }).getByRole("link", { name: short }).click();
  await expect.poll(shown).toBeLessThan(longUsed / 3);
  // The bug: while the short conversation is answering, leaving and coming back showed the long one's numbers.
  await composer(page).fill("[slow] a long answer");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Stop generating" })).toBeVisible();
  await page.getByRole("navigation", { name: "Conversations" }).getByRole("link", { name: long }).click();
  await expect.poll(shown).toBeGreaterThan(shortUsed * 3);
  await page.getByRole("navigation", { name: "Conversations" }).getByRole("link", { name: short }).click();
  await expect(page.getByRole("button", { name: "Stop generating" })).toBeVisible(); // still answering
  await expect.poll(shown).toBeLessThan(longUsed / 3);
  await expect(page.getByTestId("send-button")).toBeVisible({ timeout: 20_000 });
  const afterAnswer = await api(short);
  await expect.poll(async () => near(await shown(), afterAnswer)).toBeTruthy();
  // After a reload.
  await page.reload();
  await expect(indicator).toBeVisible();
  expect(near(await shown(), afterAnswer)).toBeTruthy();
  await page.goto(`/c/${long}`);
  await expect.poll(shown).toBeGreaterThan(shortUsed * 3);
});
