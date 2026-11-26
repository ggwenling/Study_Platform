import { defineConfig, devices } from '@playwright/test'

const outputDir = process.env.UX_PLAYWRIGHT_OUTPUT || '../../.local/ux-acceptance/playwright-output'

export default defineConfig({
  testDir: './journeys',
  testMatch: '**/*.e2e.ts',
  timeout: 180_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: false,
  reporter: [
    ['list'],
    ['json', { outputFile: process.env.UX_PLAYWRIGHT_JSON || '../../.local/ux-acceptance/playwright-report.json' }],
  ],
  outputDir,
  use: {
    baseURL: process.env.UX_BASE_URL || 'http://127.0.0.1:5173',
    actionTimeout: 15_000,
    navigationTimeout: 20_000,
    locale: 'zh-CN',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
})
