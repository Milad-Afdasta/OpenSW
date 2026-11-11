// Real-server selected-resource ownership and bounded collection behavior.
const { test, expect } = require('../../web/node_modules/@playwright/test');
const fs = require('node:fs');
const crypto = require('node:crypto');

if (!process.env.OPENSW_QA_CONFIG) throw new Error('Set the private browser accounts file.');
const config = JSON.parse(fs.readFileSync(process.env.OPENSW_QA_CONFIG, 'utf8'));
const apps = process.env.OPENSW_QA_APP ? [process.env.OPENSW_QA_APP] : ['projects', 'chat'];
const cookies = new Map();
const nonce = () => 'production-' + crypto.randomBytes(6).toString('hex');

async function signIn(page, app, info) {
  const account = config[app];
  if (!account?.url || !account.username || !account.password) throw new Error('Missing selected private app account.');
  await page.addInitScript(theme => localStorage.setItem('OpenSW-theme', theme), info.project.use.colorScheme);
  if (cookies.has(app)) await page.context().addCookies(cookies.get(app));
  await page.goto(account.url);
  try {
    if (!cookies.has(app)) {
      await page.getByLabel('Username', { exact: true }).fill(account.username);
      await page.getByLabel('Password', { exact: true }).fill(account.password);
      const response = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/auth/login');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      if ((await response).status() !== 200) throw new Error('Rejected sign-in.');
    }
    await expect(page.getByRole('button', { name: 'Open account menu', exact: true })).toBeVisible();
    const confirmed = await page.context().cookies(account.url);
    if (new URL(account.url).protocol === 'https:') expect(confirmed.filter(cookie => cookie.name.startsWith('opensw_')).every(cookie => cookie.secure)).toBe(true);
    cookies.set(app, confirmed);
  } catch {
    await page.getByLabel('Password', { exact: true }).fill('').catch(() => {});
    throw new Error('Production QA sign-in did not complete; accessible credential field cleared, original action details omitted.');
  }
}

async function api(page, method, path, body) {
  const response = await page.evaluate(async ({ method, path, body }) => {
    const session = await (await fetch('/api/me')).json();
    const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }, { method, path, body });
  expect([200, 201], `${method} ${path} must return a confirmed result`).toContain(response.status);
  return response.body;
}

const contracts = {
  projects: { collection: '/api/projects', detail: '/api/projects', parameter: 'project', navigation: 'Projects', reload: 'Reload project' },
  chat: { collection: '/api/chat/channels', detail: '/api/chat/conversations', parameter: 'conversation', navigation: 'Channels', reload: 'Reload conversation' },
};

async function navigation(page, contract) {
  const open = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await open.isVisible()) await open.click();
  return page.getByRole('navigation', { name: contract.navigation, exact: true });
}

async function select(page, contract, item) {
  const sidebar = await navigation(page, contract);
  const target = sidebar.getByRole('button', { name: item.name, exact: true });
  const seen = new Set();
  for (let count = 0; count < 20 && !await target.isVisible(); count++) {
    const more = sidebar.getByRole('button', { name: 'Load more', exact: true });
    if (!await more.isVisible()) break;
    const previousButtons = await sidebar.getByRole('button').count();
    const response = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === contract.collection && new URL(response.url()).searchParams.has('cursor'));
    await more.click();
    const next = await response;
    expect(next.status()).toBe(200);
    const cursor = new URL(next.url()).searchParams.get('cursor');
    expect(seen.has(cursor), 'Each explicit pagination action must advance its cursor').toBe(false);
    seen.add(cursor);
    const value = await next.json();
    // A network response precedes React's append. On the final page Load more
    // disappears, so waiting on its enabled state can trap a poll on a removed
    // element even after the requested channel has rendered.
    await expect(sidebar.getByRole('button')).toHaveCount(previousButtons + value.items.length - (value.next_cursor === null ? 1 : 0));
  }
  await expect(target).toBeVisible();
  await target.scrollIntoViewIfNeeded();
  await target.click();
}

function ready(page, app) {
  return app === 'projects' ? page.getByRole('button', { name: 'New task', exact: true }) : page.getByLabel('Message', { exact: true });
}

for (const app of ['projects', 'chat'].filter(app => apps.includes(app))) {
  const contract = contracts[app];
  test(`${app}: detail failure, wrong identity, retry and late completion keep the current selection owner`, async ({ page }, info) => {
    await signIn(page, app, info);
    const prefix = nonce();
    const a = await api(page, 'POST', contract.collection, { name: prefix + '-a' });
    const b = await api(page, 'POST', contract.collection, { name: prefix + '-b' });
    let attempts = 0;
    const aPath = `${contract.detail}/${a.id}`;
    const aIntercept = async route => {
      attempts++;
      if (attempts === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'QA selected detail is temporarily unavailable' }) });
      if (attempts === 2) {
        const response = await route.fetch({ maxRedirects: 0 });
        expect(response.status()).toBe(200);
        const real = await response.json();
        return route.fulfill({ response, json: { ...real, id: b.id } });
      }
      return route.continue();
    };
    let release;
    const released = new Promise(resolve => { release = resolve; });
    let started, finished;
    const held = new Promise(resolve => { started = resolve; });
    const done = new Promise(resolve => { finished = resolve; });
    const bPath = `${contract.detail}/${b.id}`;
    let dependentB = 0;
    page.on('request', request => {
      const path = new URL(request.url()).pathname;
      if (path === (app === 'projects' ? `${bPath}/tasks` : `${bPath}/messages`) || path === `${bPath}/read`) dependentB++;
    });
    const bIntercept = async route => {
      try {
        const response = await route.fetch({ maxRedirects: 0 });
        expect(response.status()).toBe(200);
        started();
        await released;
        // The old selection's AbortController may already have cancelled this
        // browser request. Either cancellation or a late response must leave A.
        await route.fulfill({ response }).catch(() => {});
      } finally { finished(); }
    };
    try {
      await page.route('**' + aPath, aIntercept);
      await page.goto(config[app].url + `?${contract.parameter}=${a.id}`);
      await expect(page.getByRole('alert').filter({ hasText: 'QA selected detail is temporarily unavailable' })).toBeVisible();
      await expect(ready(page, app)).toHaveCount(0);
      await page.screenshot({ path: info.outputPath(`${app}-detail-error-${info.project.name}.png`), fullPage: true });
      await page.getByRole('button', { name: contract.reload, exact: true }).click();
      await expect(page.getByRole('alert').filter({ hasText: /different resource/i })).toBeVisible();
      await expect(ready(page, app)).toHaveCount(0);
      await page.getByRole('button', { name: contract.reload, exact: true }).click();
      await expect(page.getByRole('heading', { name: a.name, exact: true })).toBeVisible();
      await expect(ready(page, app)).toBeVisible();
      expect(attempts).toBe(3);
      await page.unroute('**' + aPath, aIntercept);
      await page.route('**' + bPath, bIntercept);
      await select(page, contract, b);
      await held;
      await expect(page.getByRole('heading', { name: a.name, exact: true })).toHaveCount(0);
      await expect(ready(page, app)).toHaveCount(0);
      await select(page, contract, a);
      await expect(page.getByRole('heading', { name: a.name, exact: true })).toBeVisible();
      release();
      await done;
      await expect(page.getByRole('heading', { name: a.name, exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: b.name, exact: true })).toHaveCount(0);
      expect(new URL(page.url()).searchParams.get(contract.parameter)).toBe(String(a.id));
      expect(dependentB, 'Unconfirmed B detail must not start dependent reads or read-receipt writes').toBe(0);
      await page.screenshot({ path: info.outputPath(`${app}-late-detail-${info.project.name}.png`), fullPage: true });
    } finally {
      release();
      await page.unroute('**' + aPath, aIntercept).catch(() => {});
      await page.unroute('**' + bPath, bIntercept).catch(() => {});
      if (app === 'projects' && !page.isClosed()) {
        await api(page, 'PATCH', `/api/projects/${a.id}`, { archived: true });
        await api(page, 'PATCH', `/api/projects/${b.id}`, { archived: true });
      }
    }
  });

  test(`${app}: deep detail never scans sidebar pages; explicit pagination remains independent`, async ({ page }, info) => {
    await signIn(page, app, info);
    const prefix = nonce();
    const toArchive = new Set();
    const requests = [];
    let selected;
    try {
      // Own the full page precondition. Other independent QA actors may
      // archive their own projects while this scenario is running.
      for (let index = 0; index < 100; index++) {
        const row = await api(page, 'POST', contract.collection, { name: `${prefix}-seed-${index}` });
        if (app === 'projects') toArchive.add(row.id);
      }
      selected = await api(page, 'POST', contract.collection, { name: prefix + '-deep' });
      if (app === 'projects') toArchive.add(selected.id);
      page.on('request', request => {
        if (request.method() === 'GET' && new URL(request.url()).pathname === contract.collection) requests.push(new URL(request.url()));
      });
      await page.goto(config[app].url + `?${contract.parameter}=${selected.id}`);
      await expect(page.getByRole('heading', { name: selected.name, exact: true })).toBeVisible();
      await expect(ready(page, app)).toBeVisible();
      const sidebar = await navigation(page, contract);
      await expect(sidebar.getByRole('button', { name: 'Load more', exact: true })).toBeVisible();
      await expect(sidebar.getByRole('button', { name: selected.name, exact: true })).toHaveCount(0);
      expect(requests.filter(url => url.searchParams.has('cursor')), 'Selected metadata must not trigger automatic collection scans').toHaveLength(0);
      const seen = new Set();
      for (let step = 0; step < 20 && !await sidebar.getByRole('button', { name: selected.name, exact: true }).isVisible(); step++) {
        const prior = requests.length;
        const response = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === contract.collection && new URL(response.url()).searchParams.has('cursor'));
        await sidebar.getByRole('button', { name: 'Load more', exact: true }).click();
        const confirmed = await response;
        expect(confirmed.status()).toBe(200);
        const cursor = new URL(confirmed.url()).searchParams.get('cursor');
        expect(seen.has(cursor)).toBe(false);
        seen.add(cursor);
        expect(requests.length, 'One explicit pagination action issues one collection read').toBe(prior + 1);
        await expect(page.getByRole('heading', { name: selected.name, exact: true })).toBeVisible();
        const value = await confirmed.json();
        if (value.next_cursor !== null) await expect(sidebar.getByRole('button', { name: 'Load more', exact: true })).toBeEnabled();
        else break;
      }
      await expect(sidebar.getByRole('button', { name: selected.name, exact: true })).toBeVisible();
      expect(seen.size).toBeGreaterThan(0);
      if (info.project.use.isMobile) await page.getByRole('button', { name: 'Close navigation', exact: true }).first().click();
      await expect(ready(page, app)).toBeVisible();
      if (app === 'projects') {
        toArchive.delete(selected.id); // Never replay an uncertain archive command.
        await api(page, 'PATCH', `/api/projects/${selected.id}`, { archived: true });
        requests.length = 0;
        await page.goto(config.projects.url + `?project=${selected.id}&view=archive`);
        await expect(page.getByRole('heading', { name: selected.name, exact: true })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Restore project', exact: true })).toBeVisible();
        await expect(ready(page, app)).toHaveCount(0);
        expect(requests.filter(url => url.searchParams.has('cursor'))).toHaveLength(0);
      }
      await page.screenshot({ path: info.outputPath(`${app}-manual-pagination-${info.project.name}.png`), fullPage: true });
    } finally {
      if (app === 'projects' && !page.isClosed()) for (const id of toArchive) await api(page, 'PATCH', `/api/projects/${id}`, { archived: true });
    }
  });
}

if (apps.includes('projects')) {
  test('projects: task fields and comments require the canonical selected parent', async ({ page }, info) => {
    await signIn(page, 'projects', info);
    const prefix = nonce();
    const a = await api(page, 'POST', '/api/projects', { name: prefix + '-parent-a' });
    const b = await api(page, 'POST', '/api/projects', { name: prefix + '-parent-b' });
    const task = await api(page, 'POST', `/api/projects/${a.id}/tasks`, { title: prefix + '-task', description: prefix + '-body' });
    const comment = await api(page, 'POST', `/api/tasks/${task.id}/comments`, { body: prefix + '-comment' });
    const reads = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === `/api/tasks/${task.id}/comments`) reads.push(request.method());
    });
    try {
      await page.goto(config.projects.url + `?project=${b.id}&task=${task.id}`);
      const dialog = page.getByRole('dialog', { name: 'Task details', exact: true });
      await expect(page.getByRole('heading', { name: b.name, exact: true })).toBeVisible();
      await expect(dialog.getByRole('alert')).toContainText('does not belong to the selected project');
      await expect(dialog.getByLabel('Task title', { exact: true })).toHaveCount(0);
      await expect(dialog.getByLabel('Write a comment', { exact: true })).toHaveCount(0);
      expect(reads, 'Wrong-parent task metadata must not authorize dependent comment reads').toHaveLength(0);
      await page.screenshot({ path: info.outputPath(`projects-task-parent-error-${info.project.name}.png`), fullPage: true });
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(dialog).toHaveCount(0);
      // Sidebar archive filtering cannot change the actual active project's
      // edit permissions. The canonical project owns its archived state.
      await page.goto(config.projects.url + `?project=${a.id}&task=${task.id}&view=archive`);
      await expect(dialog.getByLabel('Task title', { exact: true })).toHaveValue(task.title);
      await expect(dialog.getByLabel('Task title', { exact: true })).toBeEnabled();
      await expect(dialog.getByText(comment.body, { exact: true })).toBeVisible();
      const actual = await api(page, 'GET', `/api/tasks/${task.id}`);
      expect(actual).toEqual(task);
      await page.screenshot({ path: info.outputPath(`projects-task-parent-confirmed-${info.project.name}.png`), fullPage: true });
    } finally {
      await api(page, 'PATCH', `/api/projects/${a.id}`, { archived: true });
      await api(page, 'PATCH', `/api/projects/${b.id}`, { archived: true });
    }
  });
}
