const { defineConfig } = require('../../web/node_modules/@playwright/test');
const path = require('node:path');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.cjs',
  workers: 1,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  outputDir: process.env.OPENSW_BROWSER_ARTIFACTS || path.resolve(__dirname, '../../artifacts/browser'),
  reporter: [['list']],
  use: {
    browserName: 'chromium',
    headless: true,
    screenshot: 'only-on-failure',
    // Trace files can contain entered credentials. Screenshots show masked
    // password controls; record no raw credential-bearing action trace.
    trace: 'off',
  },
  projects: [
    { name: 'desktop-light', use: { viewport: { width: 1440, height: 900 }, colorScheme: 'light' } },
    { name: 'desktop-dark', use: { viewport: { width: 1440, height: 900 }, colorScheme: 'dark' } },
    { name: 'mobile-light', use: { viewport: { width: 390, height: 844 }, colorScheme: 'light', isMobile: true, hasTouch: true } },
    { name: 'mobile-dark', use: { viewport: { width: 390, height: 844 }, colorScheme: 'dark', isMobile: true, hasTouch: true } },
  ],
});
