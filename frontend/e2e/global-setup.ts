import { mkdirSync } from "node:fs";
import { request, type FullConfig } from "@playwright/test";

export const ADMIN = { email: "admin@example.com", username: "admin", password: "e2e quiet passphrase" };

/** Create the first account (the administrator) and save its signed-in state. */
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL as string;
  mkdirSync("test-results", { recursive: true });
  const ctx = await request.newContext({ baseURL });
  await ctx.get("/api/auth/status");
  const csrf = (await ctx.storageState()).cookies.find((c) => c.name === "chimera_csrf")!.value;
  const resp = await ctx.post("/api/auth/register", {
    headers: { "X-CSRF-Token": csrf },
    data: { ...ADMIN, password_confirm: ADMIN.password, language: "en" },
  });
  if (!resp.ok()) throw new Error(`could not create admin: ${resp.status()} ${await resp.text()}`);
  await ctx.storageState({ path: "test-results/admin-state.json" });
  await ctx.dispose();
}
