import { expect, test, type Page } from "@playwright/test";

const composer = (page: Page) => page.getByLabel("Message", { exact: true });

async function newChat(page: Page) {
  await page.goto("/");
  await expect(composer(page)).toBeVisible();
  await expect(page.getByRole("button", { name: /^Model: qwen2\.5/ })).toBeVisible();
}

async function send(page: Page, text: string) {
  await composer(page).fill(text);
  await page.keyboard.press("Enter");
  await expect(page.getByText(`You said: ${text}`)).toBeVisible();
}

const words = (n: number, tag: string) => Array.from({ length: n }, (_, i) => `${tag}${i}`).join(" ");

async function setContextOverride(page: Page, value: string) {
  await page.goto("/models");
  await page.getByRole("button", { name: "More options for llama-server" }).click();
  await page.getByRole("menuitem", { name: "Edit" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Advanced" }).click();
  await dialog.getByLabel("Context size override (tokens)").fill(value);
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toBeHidden();
}

test("branching: edit and regenerate create versions; the navigator switches and the choice persists", async ({ page }) => {
  await newChat(page);
  await send(page, "first question");
  await send(page, "second question");

  // Edit the first message: a new version, the old branch stays reachable.
  await page.getByText("first question", { exact: true }).hover();
  await page.getByRole("button", { name: "Edit" }).first().click();
  await page.getByLabel("Edit message").fill("first question, rephrased");
  await page.getByRole("button", { name: "Save and resubmit" }).click();
  await expect(page.getByText("You said: first question, rephrased")).toBeVisible();
  await expect(page.getByText("second question", { exact: true })).toHaveCount(0);
  const versions = page.getByRole("group", { name: "Version 2 of 2" });
  await expect(versions).toBeVisible();

  await versions.getByRole("button", { name: "Previous version" }).click();
  await expect(page.getByRole("group", { name: "Version 1 of 2" })).toBeVisible();
  await expect(page.getByText("You said: second question")).toBeVisible();

  // The active branch is stored on the server: a reload shows the same one.
  await page.reload();
  await expect(page.getByText("You said: second question")).toBeVisible();
  await expect(page.getByRole("group", { name: "Version 1 of 2" })).toBeVisible();

  // Regenerate: a second response, reachable with the keyboard.
  await page.getByRole("button", { name: "Regenerate" }).last().click();
  const responses = page.getByRole("group", { name: "Response 2 of 2" });
  await expect(responses).toBeVisible();
  await responses.getByRole("button", { name: "Previous response" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("group", { name: "Response 1 of 2" })).toBeVisible();
});

test("context: a small window trims older messages visibly, pinning keeps one, the indicator explains; Lithuanian at narrow width", async ({ page }) => {
  await setContextOverride(page, "400");
  try {
    await newChat(page);
    await send(page, `keep this ${words(12, "p")}`);
    for (let i = 0; i < 6; i++) await send(page, `${words(20, `m${i}w`)}`);

    const marker = page.getByTestId("context-marker");
    await expect(marker).toBeVisible();
    await expect(marker).toContainText(/earlier messages? (is|are) not included in the model's context/);

    // Pin the first question, then send again: it stays in the context.
    await page.getByText(/^keep this p0/).hover();
    await page.getByRole("button", { name: "Pin to context" }).first().click();
    await expect(page.getByText("Pinned", { exact: true })).toBeVisible();
    await send(page, "after pinning");

    const indicator = page.getByTestId("context-indicator");
    await expect(indicator).toHaveAccessibleName(/^Context window usage: .+ \/ 400$/);
    await indicator.click();
    const details = page.getByRole("dialog");
    await expect(details.getByText("Pinned messages")).toBeVisible();
    await expect(details.getByText(/Context window: 400 tokens \(set manually\)/)).toBeVisible();
    await page.keyboard.press("Escape");

    // "Change strategy" opens the conversation settings on the context option.
    await marker.getByRole("button", { name: "Change strategy" }).click();
    await expect(page.getByLabel("Context strategy")).toBeVisible();
    await page.keyboard.press("Escape");

    // Lithuanian, narrow: marker, navigator and indicator fit without horizontal scrolling.
    const url = page.url();
    await page.goto("/settings/appearance");
    await page.getByRole("combobox", { name: "Language" }).selectOption("lt");
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto(url);
    await expect(page.getByTestId("context-marker").first()).toContainText(/neįtraukt/);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.goto("/settings/chat");
    await expect(page.getByText("Konteksto langas").first()).toBeVisible();
    const settingsOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(settingsOverflow).toBeLessThanOrEqual(0);
  } finally {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/settings/appearance");
    await page.locator("select").first().selectOption("en");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await setContextOverride(page, "");
  }
});
