import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  use: {
    baseURL: "http://127.0.0.1:8017",
    headless: true,
  },
  webServer: {
    command: '"..\\.venv\\Scripts\\python.exe" ..\\serve.py --host 127.0.0.1 --port 8017',
    url: "http://127.0.0.1:8017",
    reuseExistingServer: false,
    timeout: 120000,
  },
});