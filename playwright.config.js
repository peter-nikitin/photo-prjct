const { defineConfig, devices } = require('@playwright/test');

const isCI = process.env.CI === 'true';

module.exports = defineConfig({
  testDir: 'tests/visual',
  testMatch: 'visual.spec.js',
  timeout: 60_000,
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1,
  reporter: [['html', { outputFolder: 'playwright-report', open: 'never' }]],
  outputDir: 'test-results',
  snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{ext}',
  expect: {
    toHaveScreenshot: {
      animations: 'disabled',
      maxDiffPixelRatio: 0.01,
      threshold: 0.2,
    },
  },
  use: {
    baseURL: 'http://127.0.0.1:8001',
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    colorScheme: 'light',
    reducedMotion: 'reduce',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command:
      'PYTHONPATH=.:src/backend DJANGO_SETTINGS_MODULE=tests.visual.settings python src/backend/manage.py migrate --noinput && PYTHONPATH=.:src/backend DJANGO_SETTINGS_MODULE=tests.visual.settings python src/backend/manage.py runserver 127.0.0.1:8001 --noreload',
    url: 'http://127.0.0.1:8001/__visual__/catalog/empty/',
    reuseExistingServer: !isCI,
    timeout: 120_000,
  },
});
