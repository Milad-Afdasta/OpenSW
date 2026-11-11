// Optional headed Chromium lifecycle proof. Restores native focus behavior that
// Playwright normally overrides; never overwrites document visibility or focus.
const { chromium, expect } = require('../../web/node_modules/@playwright/test');
const fs = require('node:fs/promises');

(async () => {
  if (!process.env.OPENSW_QA_CONFIG) throw new Error('Set OPENSW_QA_CONFIG; see tests/browser/README.md.');
  const { chat: config } = JSON.parse(await fs.readFile(process.env.OPENSW_QA_CONFIG, 'utf8'));
  if (!config) throw new Error('The private configuration must contain Chat.');
  const browser = await chromium.launch({ headless: false, ignoreDefaultArgs: [
    '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
  ] });
  const result = { nativeFocus: false, unfocusedPolling: false, unfocusedReadReceipt: false, focusRecovery: false, malformedCsrf: false };
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(config.url);
    try {
      await page.getByLabel('Username', { exact: true }).fill(config.username);
      await page.getByLabel('Password', { exact: true }).fill(config.password);
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Open account menu' })).toBeVisible();
    } catch {
      throw new Error('Chat sign-in did not reach the app. Check the private credentials, server logs and sign-in rate window.');
    }
    const command = async (method, path, payload) => page.evaluate(async ({ method, path, payload }) => {
      const session = await (await fetch('/api/me')).json();
      const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body: payload === undefined ? undefined : JSON.stringify(payload) });
      return { status: response.status, body: await response.json() };
    }, { method, path, payload });
    const prefix = `focus-${Date.now().toString(36)}`;
    const channel = await command('POST', '/api/chat/channels', { name: prefix });
    expect(channel.status).toBe(201);
    const route = `/api/chat/conversations/${channel.body.id}`;
    await page.goto(`${config.url}/?conversation=${channel.body.id}`);
    await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
    const session = await context.newCDPSession(page);
    await session.send('Emulation.setFocusEmulationEnabled', { enabled: false });
    await page.bringToFront();
    await page.getByLabel('Message', { exact: true }).click();
    await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(true);
    let polls = 0, reads = 0;
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (path === `${route}/messages` && request.method() === 'GET') polls++;
      if (path === `${route}/read` && request.method() === 'POST') reads++;
    });
    const other = await context.newPage();
    await other.setContent('<button>Another browser tab</button>');
    const otherSession = await context.newCDPSession(other);
    await otherSession.send('Emulation.setFocusEmulationEnabled', { enabled: false });
    await other.bringToFront();
    await other.getByRole('button').click();
    await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(false);
    result.nativeFocus = true;
    // Let any request issued before the focus transition finish, then seed a
    // confirmed message without navigating or focusing the Chat page.
    await page.waitForTimeout(350);
    const message = await command('POST', `${route}/messages`, { body: `${prefix} arrives while unfocused` });
    expect(message.status).toBe(201);
    const baseline = { polls, reads };
    await page.waitForTimeout(5500);
    expect(polls).toBe(baseline.polls);
    expect(reads).toBe(baseline.reads);
    result.unfocusedPolling = result.unfocusedReadReceipt = true;
    await page.bringToFront();
    await page.getByLabel('Message', { exact: true }).click();
    await expect(page.getByText(message.body.body, { exact: true })).toBeVisible();
    await expect.poll(() => polls).toBeGreaterThan(baseline.polls);
    await expect.poll(() => reads).toBeGreaterThan(baseline.reads);
    result.focusRecovery = true;
    const rejected = await page.evaluate(async () => {
      const response = await fetch('/api/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': '\u00e9' } });
      return { status: response.status, stillSignedIn: (await fetch('/api/me')).status };
    });
    expect(rejected).toEqual({ status: 403, stillSignedIn: 200 });
    result.malformedCsrf = true;
    expect((await command('DELETE', `/api/chat/messages/${message.body.id}`)).status).toBe(200);
    expect((await command('POST', '/api/auth/logout')).status).toBe(200);
  } finally {
    await browser.close();
    console.log(JSON.stringify(result));
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
