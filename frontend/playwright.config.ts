import { defineConfig } from "@playwright/test";

// The suite runs against a live stack: `docker compose up`, or `make dev` for local processes.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // tests share one controller, so they must not run in parallel
  use: {
    baseURL: process.env.BDO_UI_URL ?? "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 900 },
  },
  reporter: [["list"]],
});
