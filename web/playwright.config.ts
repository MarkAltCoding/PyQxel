// Browser tests: the built app, served by the real API on a seeded throwaway database
// (../scripts/e2e-server.sh), driven in Chromium at a desktop and a phone's size.
// Build first (`npm run build`), then `npm run e2e`.
import { defineConfig, devices } from '@playwright/test'

const port = Number(process.env.E2E_PORT ?? 8765)

export default defineConfig({
  testDir: './e2e',
  // One server and one database: tests that change the account run one at a time.
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    // A phone's width in Chromium, so CI needs one browser: nothing may scroll sideways.
    { name: 'phone', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: '../scripts/e2e-server.sh',
    url: `http://127.0.0.1:${port}/health`,
    timeout: 120_000,
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
