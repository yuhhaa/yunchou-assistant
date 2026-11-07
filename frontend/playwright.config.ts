import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:5175",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --port 5175",
    env: { VITE_AMAP_JS_KEY: "00000000000000000000000000000000", VITE_AMAP_SERVICE_HOST: "/_AMapService" },
    url: "http://127.0.0.1:5175",
    reuseExistingServer: true,
  },
});
