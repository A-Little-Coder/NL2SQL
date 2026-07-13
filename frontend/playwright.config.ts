import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E 配置（change clarify-choice-inspector-cancel）
 *
 * 前置：后端需先启动（python run_api.py，监听 :8000），Vite proxy /api/v1 -> :8000。
 * 本配置只自动起前端 dev server；后端请手动起。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: 'list',
  timeout: 60000,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    actionTimeout: 15000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 30000,
  },
});
