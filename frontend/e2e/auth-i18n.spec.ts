import { expect, test } from "@playwright/test";

// These tests start signed out.
test.use({ storageState: { cookies: [], origins: [] } });

test("unauthenticated visitors are sent to sign in, keeping the destination", async ({ page }) => {
  await page.goto("/settings/appearance");
  await expect(page).toHaveURL(/\/login\?next=%2Fsettings%2Fappearance$/);
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await page.getByLabel("Email or login name").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("e2e quiet passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/settings\/appearance$/);
});

test("API is closed without a session", async ({ request }) => {
  expect((await request.get("/api/conversations")).status()).toBe(401);
  expect((await request.get("/api/models")).status()).toBe(401);
});

test("wrong password shows a generic, localized error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email or login name").fill("admin");
  await page.getByLabel("Password", { exact: true }).fill("not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Incorrect login or password.");
});

test("register → chat → Lithuanian → reload → logout → login → streaming still works", async ({ page }) => {
  const user = { email: "ruta@example.com", username: "ruta", password: "upė miškas kalnai" };
  await page.goto("/login");
  await page.getByRole("link", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();

  // Inline validation before submitting
  await page.getByLabel("Email").fill("not-an-email");
  await page.getByLabel("Login name").fill(user.username);
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByText("Enter a valid email address.")).toBeVisible();
  await expect(page.getByText("Use at least 10 characters.")).toBeVisible();

  await page.getByLabel("Email").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByLabel("Confirm password").fill(user.password);
  await page.getByRole("button", { name: "Create account" }).click();

  // Lands in chat; the new user sees no one else's conversations.
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();
  await expect(page.getByText("No conversations yet.")).toBeVisible();

  // Switch to Lithuanian in Settings → Appearance: applies instantly.
  await page.goto("/settings/appearance");
  await page.getByRole("combobox", { name: "Language" }).selectOption("lt");
  await expect(page.getByRole("heading", { name: "Nustatymai" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "lt");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Nustatymai" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "lt");

  // Sign out from the account menu.
  await page.getByRole("button", { name: `Naudotojo ${user.username} paskyros meniu` }).click();
  await page.getByRole("menuitem", { name: "Atsijungti" }).click();
  await expect(page.getByRole("heading", { name: "Sveiki sugrįžę" })).toBeVisible();

  // A fresh browser (no local preference) still gets Lithuanian after login: it's on the account.
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.getByRole("combobox", { name: /Language|Kalba/ }).selectOption("en");
  await page.getByLabel("Email or login name").fill(user.email);
  await page.getByLabel("Password", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Naujas pokalbis" })).toBeVisible();

  // Chat streams through the shared llama-server connection created for the admin.
  await page.getByLabel("Žinutė", { exact: true }).fill("Labas iš e2e");
  await page.keyboard.press("Enter");
  await expect(page.getByText("You said: Labas iš e2e")).toBeVisible();
  await expect(page.getByText(/žet\.\/s/)).toBeVisible();
});

test("at login the account language wins; Lithuanian layout holds at narrow widths", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.goto("/login");
  await page.getByRole("combobox", { name: "Language" }).selectOption("lt");
  await page.getByLabel("El. paštas arba prisijungimo vardas").fill("admin");
  await page.getByLabel("Slaptažodis", { exact: true }).fill("e2e quiet passphrase");
  await page.getByRole("button", { name: "Prisijungti" }).click();
  // The admin's saved preference is English, so the UI switches back after sign-in.
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();
  await page.goto("/settings/appearance");
  await page.getByRole("combobox", { name: "Language" }).selectOption("lt");
  await expect(page.locator("html")).toHaveAttribute("lang", "lt");
  for (const path of ["/", "/models", "/settings/account", "/settings/users", "/settings/data", "/prompts", "/knowledge"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0);
  }
  await expect(page.getByRole("heading", { name: "Žinios" })).toBeVisible();
  // Restore English for the admin used by the other tests.
  await page.goto("/settings/appearance");
  await page.getByRole("combobox", { name: "Kalba" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});
