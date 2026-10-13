import { expect, test } from "@playwright/test";

test("theme and sidebar preferences persist across reloads", async ({ page }) => {
  await page.goto("/settings/appearance");
  await page.getByRole("radio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.getByRole("switch", { name: "Collapse sidebar" }).click();
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByRole("button", { name: "Expand sidebar" })).toBeVisible();
  // restore
  await page.getByRole("button", { name: "Expand sidebar" }).click();
  await page.getByRole("radio", { name: "System" }).click();
});

test("connection test reports failures and successes honestly", async ({ page }) => {
  await page.goto("/models");
  await expect(page.getByText("llama-server", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add connection" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Name").fill("Broken server");
  await dialog.getByLabel("Server URL").fill("http://127.0.0.1:9");
  await dialog.getByRole("button", { name: "Test connection" }).click();
  await expect(dialog.getByText("Connection failed")).toBeVisible();
  await expect(dialog.getByText(/Could not connect/)).toBeVisible();
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Broken server", { exact: true })).toBeVisible();
  await expect(page.getByText("Unreachable")).toBeVisible();

  // The working server shows verified status and real diagnostics.
  await page.getByRole("button", { name: "Test" }).first().click();
  await expect(page.getByText("server properties available")).toBeVisible();
  await expect(page.getByText("b9999-mock")).toBeVisible();
});

test("prompt library: create and insert into the composer", async ({ page }) => {
  await page.goto("/prompts");
  await page.getByRole("button", { name: "New prompt" }).first().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Title").fill("Haiku writer");
  await dialog.getByLabel("Category").fill("Writing");
  await dialog.getByLabel("Prompt").fill("Write a haiku about the sea.");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Haiku writer")).toBeVisible();
  await page.getByRole("button", { name: "Use" }).click();
  await expect(page.getByLabel("Message", { exact: true })).toHaveValue("Write a haiku about the sea.");
});

test("knowledge collection: upload, search and use in chat with sources", async ({ page }) => {
  await page.goto("/knowledge");
  await page.getByRole("button", { name: "New collection" }).click();
  await page.getByRole("dialog").getByLabel("Name").fill("Reactor manual");
  await page.getByRole("dialog").getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "Reactor manual" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "reactor.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("The Zephyr reactor core runs at 650 degrees.\n\nMaintenance happens every 18 months."),
  });
  await expect(page.getByText("reactor.md")).toBeVisible();
  await page.getByLabel("Search query").fill("maintenance schedule");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText("Maintenance happens every 18 months.").first()).toBeVisible();

  await page.goto("/");
  await page.getByRole("button", { name: "Conversation settings" }).click();
  await page.getByRole("radio", { name: /Knowledge/ }).click();
  await page.getByText("Reactor manual").click();
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByLabel("Message", { exact: true }).fill("What temperature does the Zephyr reactor core run at?");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Sources")).toBeVisible();
  await expect(page.getByRole("button", { name: /reactor\.md/ })).toBeVisible();
});
