import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run the real backend against the mock llama-server.
// No model download or network access is needed.
const PY = process.env.CHIMERA_PYTHON ?? "../backend/.venv/bin/python";
const MOCK_PORT = 18080;
// Mock web pages + SearXNG-compatible search; results link back to itself.
const WEB_PORT = 18090;
const APP_PORT = 18700;
const DATA = "test-results/e2e-data";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}`,
    // Every test starts signed in as the administrator created in global setup.
    storageState: "test-results/admin-state.json",
    locale: "en-US",
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } }],
  webServer: [
    {
      command: `${PY} ../backend/tests/mock_llama_server.py --port ${MOCK_PORT} --model qwen2.5-7b-instruct-q4_k_m.gguf --model llama-3.2-3b-instruct-q8_0.gguf`,
      url: `http://127.0.0.1:${MOCK_PORT}/health`,
      reuseExistingServer: false,
    },
    {
      command: `${PY} ../backend/tests/mock_web.py --port ${WEB_PORT} --self-links`,
      url: `http://127.0.0.1:${WEB_PORT}/plain.txt`,
      reuseExistingServer: false,
    },
    {
      command: `rm -rf ${DATA} && ${PY} -m chimera`,
      cwd: ".",
      url: `http://127.0.0.1:${APP_PORT}/api/health`,
      reuseExistingServer: false,
      env: {
        PYTHONPATH: "../backend",
        CHIMERA_PORT: String(APP_PORT),
        CHIMERA_DATA_DIR: DATA,
        CHIMERA_STATIC_DIR: "dist",
        CHIMERA_SEED_PROVIDER_URL: `http://127.0.0.1:${MOCK_PORT}`,
        CHIMERA_LOG_LEVEL: "warning",
        // Open so the auth flow test can register more accounts.
        CHIMERA_REGISTRATION_MODE: "open",
      },
    },
  ],
});
