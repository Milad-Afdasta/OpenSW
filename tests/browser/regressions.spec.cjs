const { test, expect } = require('../../web/node_modules/@playwright/test');
const fs = require('node:fs');
const crypto = require('node:crypto');

if (!process.env.OPENSW_QA_CONFIG) throw new Error('Set OPENSW_QA_CONFIG to the private browser account JSON.');
const config = JSON.parse(fs.readFileSync(process.env.OPENSW_QA_CONFIG, 'utf8'));
const apps = process.env.OPENSW_QA_APP ? [process.env.OPENSW_QA_APP] : ['files', 'projects', 'notes', 'chat'];
if (apps.some(app => !['files', 'projects', 'notes', 'chat'].includes(app))) throw new Error('Unknown OPENSW_QA_APP.');
const nonce = () => 'regression-' + crypto.randomBytes(6).toString('hex');
// Reuse only real server-issued cookies in this worker's memory. Each app has
// its own cache entry; no credentials/storage state are written to artifacts.
const confirmedCookies = new Map();

async function signIn(page, app, info) {
  const account = config[app];
  if (!account?.url || !account.username || !account.password) throw new Error(`Missing private ${app} account configuration.`);
  try {
    await page.addInitScript(theme => localStorage.setItem('OpenSW-theme', theme), info.project.use.colorScheme);
    const cached = confirmedCookies.get(app);
    if (cached) await page.context().addCookies(cached);
    await page.goto(account.url);
    if (!cached) {
      await page.getByLabel('Username', { exact: true }).fill(account.username);
      await page.getByLabel('Password', { exact: true }).fill(account.password);
      const loginResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/auth/login');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
      if ((await loginResponse).status() !== 200) throw new Error('QA sign-in was rejected.');
    }
    await expect(page.getByRole('button', { name: 'Open account menu' })).toBeVisible();
    confirmedCookies.set(app, await page.context().cookies(account.url));
  } catch {
    try {
      const password = page.getByLabel('Password', { exact: true });
      if (await password.isVisible()) await password.fill('');
    } catch { /* A closed page has no accessible credential field. */ }
    // Do not retain the original fill/click error: its action log can contain
    // the entered password even when Playwright tracing is disabled.
    throw new Error('QA sign-in did not reach the authenticated app. Credential input was cleared when accessible; original action details were omitted.');
  }
}

async function api(page, method, path, body, expected = [200, 201]) {
  const response = await page.evaluate(async ({ method, path, body }) => {
    const session = await (await fetch('/api/me')).json();
    const result = await fetch(path, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: result.status, body: await result.json() };
  }, { method, path, body });
  expect(expected, `${method} ${path}: unexpected response`).toContain(response.status);
  return response.body;
}

async function openNavigation(page) {
  const opener = page.getByRole('button', { name: 'Open navigation', exact: true });
  if (await opener.isVisible()) await opener.click();
}

async function loadSidebarPage(page, navigation, collection) {
  const rows = navigation.locator('.conversation-item');
  const before = await rows.count();
  const response = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === collection && new URL(response.url()).searchParams.has('cursor'));
  await navigation.getByRole('button', { name: 'Load more', exact: true }).click();
  const confirmed = await response;
  expect(confirmed.status()).toBe(200);
  const value = await confirmed.json();
  expect(value.items.length).toBeGreaterThan(0);
  // A response alone precedes React's commit. Wait for the actual appended
  // rows before deciding whether another user pagination action is needed.
  await expect(rows).toHaveCount(before + value.items.length);
}

async function channelButton(page, name) {
  await openNavigation(page);
  const channels = page.getByRole('navigation', { name: 'Channels', exact: true });
  const button = channels.getByRole('button', { name, exact: true });
  await expect.poll(async () => await button.isVisible() || await channels.getByRole('button', { name: 'Load more', exact: true }).isVisible()).toBe(true);
  for (let more = 0; more < 20 && !await button.isVisible(); more++) {
    const load = channels.getByRole('button', { name: 'Load more', exact: true });
    if (!await load.isVisible()) break;
    await loadSidebarPage(page, channels, '/api/chat/channels');
  }
  await expect(button).toBeVisible();
  return button;
}

async function selectConversation(page, name) {
  try {
    const button = await test.step('Open navigation and locate the confirmed channel', () => channelButton(page, name));
    await test.step('Scroll the real channel button into view and click it', async () => {
      await button.scrollIntoViewIfNeeded();
      await expect(button).toBeInViewport();
      await button.click({ timeout: 5000 });
    });
    await test.step('Observe the newly selected canonical heading', () => expect(page.getByRole('heading', { name, exact: true })).toBeVisible());
  } catch (error) {
    if (!page.isClosed()) {
      const diagnostic = await page.evaluate(targetName => {
        const button = [...document.querySelectorAll('.context-sidebar button')].find(element => element.textContent.trim() === targetName);
        const rectangle = button?.getBoundingClientRect();
        const hit = rectangle ? document.elementFromPoint(rectangle.x + rectangle.width / 2, rectangle.y + rectangle.height / 2) : null;
        return {
          url: location.href, target: targetName,
          rectangle: rectangle?.toJSON() ?? null,
          sidebarScrollTop: document.querySelector('.context-sidebar')?.scrollTop ?? null,
          hitTag: hit?.tagName ?? null,
          hitLabel: hit?.closest('button')?.getAttribute('aria-label') ?? null,
          hitText: hit?.closest('button')?.textContent.trim().slice(0, 100) ?? null,
        };
      }, name);
      console.log('Channel navigation diagnostic: ' + JSON.stringify(diagnostic));
    }
    throw error;
  }
}

for (const app of apps) {
  test(`${app}: closed mobile navigation is absent from accessibility and keyboard focus`, async ({ page }, info) => {
    await signIn(page, app, info);
    const sidebar = page.locator('.context-sidebar');
    if (info.project.use.isMobile) {
      await expect(sidebar).toBeHidden();
      // Role queries use the real browser accessibility visibility rules.
      expect(await sidebar.getByRole('button').count()).toBe(0);
      await page.getByRole('button', { name: 'Open navigation', exact: true }).focus();
      for (let step = 0; step < 8; step++) {
        await page.keyboard.press('Tab');
        await expect(page.locator('.context-sidebar:focus-within')).toHaveCount(0);
      }
      await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
      await expect(sidebar).toBeVisible();
      expect(await sidebar.getByRole('button').count()).toBeGreaterThan(0);
      await page.getByRole('button', { name: 'Close navigation', exact: true }).first().click();
      await expect(sidebar).toBeHidden();
      expect(await sidebar.getByRole('button').count()).toBe(0);
    } else {
      await expect(sidebar).toBeVisible();
      expect(await sidebar.getByRole('button').count()).toBeGreaterThan(0);
    }
  });
}

if (apps.includes('projects')) {
  test('projects: an unsent comment survives task save and guarded navigation', async ({ page }, info) => {
    await signIn(page, 'projects', info);
    const prefix = nonce();
    const project = await api(page, 'POST', '/api/projects', { name: prefix });
    const task = await api(page, 'POST', `/api/projects/${project.id}/tasks`, { title: prefix + ' task' });
    try {
      await page.goto(config.projects.url + `?project=${project.id}&task=${task.id}`);
      const details = page.getByRole('dialog', { name: 'Task details', exact: true });
      await expect(details.getByLabel('Task title', { exact: true })).toHaveValue(task.title);
      const draft = prefix + ' unsent comment';
      await details.getByLabel('Write a comment', { exact: true }).fill(draft);
      await details.getByLabel('Task title', { exact: true }).fill(prefix + ' saved title');
      await details.getByRole('button', { name: 'Save changes', exact: true }).click();
      await expect(details.getByRole('button', { name: 'Save changes', exact: true })).toBeDisabled();
      await expect(details.getByLabel('Write a comment', { exact: true })).toHaveValue(draft);
      await details.getByRole('button', { name: 'Close', exact: true }).click();
      const guard = page.getByRole('dialog', { name: 'Keep your changes?', exact: true });
      await expect(guard).toBeVisible();
      await guard.getByRole('button', { name: 'Keep editing', exact: true }).click();
      await expect(details.getByLabel('Write a comment', { exact: true })).toHaveValue(draft);
      await details.getByRole('button', { name: 'Close', exact: true }).click();
      await guard.getByRole('button', { name: 'Discard changes', exact: true }).click();
      await expect(details).toHaveCount(0);
      const stored = await api(page, 'GET', `/api/tasks/${task.id}`);
      expect(stored.title).toBe(prefix + ' saved title');
      expect((await api(page, 'GET', `/api/tasks/${task.id}/comments`)).items).toEqual([]);
      await page.screenshot({ path: info.outputPath(`projects-comment-guard-${info.project.name}.png`), fullPage: true });
    } finally {
      // Only this run's confirmed project is archived; no existing work changes.
      await api(page, 'PATCH', `/api/projects/${project.id}`, { archived: true });
    }
  });
}

if (apps.includes('chat')) {
  test('chat: committed send with a lost response stays unconfirmed in its own conversation', async ({ page }, info) => {
    await signIn(page, 'chat', info);
    const prefix = nonce();
    const a = await api(page, 'POST', '/api/chat/channels', { name: prefix + '-a' });
    const b = await api(page, 'POST', '/api/chat/channels', { name: prefix + '-b' });
    await page.goto(config.chat.url + `?conversation=${a.id}`);
    await expect(page.getByRole('heading', { name: a.name, exact: true })).toBeVisible();
    let resolveCommitted, rejectCommitted, releaseResponse;
    const committed = new Promise((resolve, reject) => { resolveCommitted = resolve; rejectCommitted = reject; });
    const release = new Promise(resolve => { releaseResponse = resolve; });
    let sendCount = 0;
    let stored;
    let flowComplete = false;
    const sendPath = `/api/chat/conversations/${a.id}/messages`;
    const intercept = async route => {
      if (route.request().method() !== 'POST') return route.continue();
      sendCount++;
      try {
        // The request reaches the real server. Only the response is lost.
        // route.fetch forwards without re-entering browser interception. Keep
        // the real origin so HTTPS verifies its certificate and hostname.
        const response = await route.fetch({
          maxRedirects: 0,
        });
        if (response.status() !== 201 && response.status() !== 200) throw new Error('The forwarded test send was rejected.');
        stored = await response.json();
        resolveCommitted(stored);
        await release;
        await route.abort('failed');
      } catch {
        rejectCommitted(new Error('The real-server send fault injection could not be completed.'));
        try { await route.abort('failed'); } catch { /* The test browser may already be closing. */ }
      }
    };
    await page.route('**' + sendPath, intercept);
    const draft = prefix + ' delivery must remain unconfirmed';
    try {
      await page.getByLabel('Message', { exact: true }).fill(draft);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      const confirmation = await test.step('The real server commits A while its acknowledgment is held', () => committed);
      expect(confirmation.conversation_id).toBe(a.id);
      await test.step('Select B using the visible navigation while A is pending', () => selectConversation(page, b.name));
      releaseResponse();
      await expect(page.getByLabel('Message', { exact: true })).toHaveValue('');
      await expect(page.getByRole('alert').filter({ hasText: /unconfirmed/i })).toHaveCount(0);
      await test.step('Return to A after losing its response', () => selectConversation(page, a.name));
      await expect(page.getByRole('alert').filter({ hasText: /unconfirmed/i })).toBeVisible();
      await expect(page.getByLabel('Message', { exact: true })).toHaveValue(draft);
      await page.getByRole('button', { name: 'Refresh conversation to check delivery', exact: true }).click();
      await expect(page.getByRole('article').getByText(draft, { exact: true })).toBeVisible();
      // Allow one real 2.5-second poll cycle; no automatic POST may follow it.
      const replay = await page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === sendPath, { timeout: 3000 }).then(() => true, () => false);
      expect(replay).toBe(false);
      expect(sendCount).toBe(1);
      const history = await api(page, 'GET', sendPath);
      expect(history.items.filter(message => message.body === draft)).toHaveLength(1);
      await expect(page.getByLabel('Message', { exact: true })).toHaveValue(draft);
      await page.screenshot({ path: info.outputPath(`chat-unconfirmed-send-${info.project.name}.png`), fullPage: true });
      flowComplete = true;
    } finally {
      releaseResponse();
      try {
        if (page.isClosed()) throw new Error('The test page closed before fixture cleanup.');
        await page.unroute('**' + sendPath, intercept);
        if (stored?.id) await api(page, 'DELETE', `/api/chat/messages/${stored.id}`);
      } catch {
        info.annotations.push({ type: 'cleanup-blocked', description: stored?.id ? `Confirmed QA message ${stored.id} may remain in conversation ${a.id}.` : 'No confirmed message ID was available for cleanup.' });
        if (flowComplete) throw new Error('Confirmed QA message cleanup was blocked; inspect this test’s fixture annotation.');
      }
    }
  });

  test('chat: fifteen channels scroll without covering direct messages', async ({ page }, info) => {
    await signIn(page, 'chat', info);
    const prefix = nonce();
    const me = await api(page, 'GET', '/api/me');
    const username = 'qa-' + crypto.randomBytes(6).toString('hex');
    const peer = await api(page, 'POST', '/api/users', {
      username, display_name: prefix + ' teammate', password: 'QA-' + crypto.randomBytes(16).toString('hex'),
    });
    expect(peer.id).not.toBe(me.user.id);
    expect(peer.username).toBe(username);
    expect(peer.role).toBe('member');
    let flowComplete = false;
    try {
      for (let index = 0; index < 15; index++) await api(page, 'POST', '/api/chat/channels', { name: `${prefix}-${String(index).padStart(2, '0')}` });
      const dm = await api(page, 'POST', '/api/chat/dms', { user_id: peer.id });
      await page.goto(config.chat.url + `?conversation=${dm.id}`);
      await expect(page.getByRole('heading', { name: peer.display_name, exact: true })).toBeVisible();
      await openNavigation(page);
      const channels = page.getByRole('navigation', { name: 'Channels', exact: true });
      const direct = page.getByRole('navigation', { name: 'Direct messages', exact: true });
      const peerButton = direct.getByRole('button', { name: peer.display_name, exact: false });
      await test.step('Load the sidebar pages independently of selected metadata', async () => {
        // Detail lookup must not scan either sidebar collection. Reach the
        // confirmed last fixture row through the explicit collection control.
        await channelButton(page, prefix + '-14');
        for (let more = 0; more < 20 && !await peerButton.isVisible(); more++) {
          const next = direct.getByRole('button', { name: 'Load more', exact: true });
          if (!await next.isVisible()) break;
          await loadSidebarPage(page, direct, '/api/chat/dms');
        }
        await expect(channels.getByRole('button').filter({ hasText: prefix })).toHaveCount(15);
        await expect(peerButton).toBeVisible();
      });
      await test.step('Reach the DM and prove the sidebar sections do not overlap', async () => {
        await peerButton.scrollIntoViewIfNeeded();
        await expect(peerButton).toBeInViewport();
        const channelBounds = await channels.boundingBox();
        const directBounds = await direct.boundingBox();
        expect(channelBounds).toBeTruthy();
        expect(directBounds).toBeTruthy();
        expect(channelBounds.y + channelBounds.height).toBeLessThanOrEqual(directBounds.y + 1);
      });
      await test.step('Reach the last fixture channel and return to the DM by native scrolling', async () => {
        const lastChannel = channels.getByRole('button').filter({ hasText: prefix + '-14' });
        await lastChannel.scrollIntoViewIfNeeded();
        await expect(lastChannel).toBeInViewport();
        // Far ends of a long sidebar need not fit in one mobile viewport.
        await expect(peerButton).toBeVisible();
        await peerButton.scrollIntoViewIfNeeded();
        await expect(peerButton).toBeInViewport();
      });
      await page.screenshot({ path: info.outputPath(`chat-sidebar-fifteen-${info.project.name}.png`), fullPage: true });
      flowComplete = true;
    } finally {
      try {
        if (page.isClosed()) throw new Error('The test page closed before fixture cleanup.');
        const current = await api(page, 'GET', `/api/users?cursor=${peer.id - 1}`);
        const confirmed = current.items.find(user => user.id === peer.id);
        expect(confirmed?.username).toBe(username);
        expect(confirmed?.role).toBe('member');
        expect(confirmed.id).not.toBe(me.user.id);
        await api(page, 'PATCH', `/api/users/${peer.id}`, { active: false });
      } catch {
        info.annotations.push({ type: 'cleanup-blocked', description: `Confirmed QA member ${peer.id} may remain active; no operator account was changed.` });
        if (flowComplete) throw new Error('Confirmed QA member cleanup was blocked; inspect this test’s fixture annotation.');
      }
    }
  });
}

if (apps.includes('chat')) {
  test('chat: a newly created channel beyond the first page resolves without Load more', async ({ page }, info) => {
    await signIn(page, 'chat', info);
    const prefix = nonce();
    const first = await api(page, 'GET', '/api/chat/channels');
    const needed = first.next_cursor === null ? Math.max(0, 101 - first.items.length) : 0;
    for (let index = 0; index < needed; index++) {
      const name = `${prefix}-seed-${index}`;
      const channel = await api(page, 'POST', '/api/chat/channels', { name });
      expect(channel.name).toBe(name);
    }
    await page.reload();
    await expect(page.getByRole('button', { name: 'Open account menu' })).toBeVisible();
    await openNavigation(page);
    await page.getByRole('button', { name: 'New channel', exact: true }).click();
    const name = prefix + '-created';
    const modal = page.getByRole('dialog', { name: 'New channel', exact: true });
    await modal.getByLabel('Channel name').fill(name);
    const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/chat/channels');
    await modal.getByRole('button', { name: 'Create channel', exact: true }).click();
    const created = await (await createdResponse).json();
    expect(created.name).toBe(name);
    // No manual pagination action is used anywhere in this boundary scenario.
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
    const selectedUrl = page.url();
    await openNavigation(page);
    await page.getByRole('button', { name: 'Refresh conversations', exact: true }).click();
    if (info.project.use.isMobile) await page.getByRole('button', { name: 'Close navigation', exact: true }).first().click();
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    await page.goto(selectedUrl);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get('conversation')).toBe(String(created.id));
    await page.screenshot({ path: info.outputPath(`chat-pagination-metadata-${info.project.name}.png`), fullPage: true });
    // Channels have no deletion API; this uniquely named QA channel remains.
  });
}

if (apps.includes('projects')) {
  test('projects: a newly created project beyond the first page resolves through refresh and reload', async ({ page }, info) => {
    await signIn(page, 'projects', info);
    const prefix = nonce();
    const ownedProjects = [];
    try {
      for (let index = 0; index < 100; index++) {
        const name = `${prefix}-seed-${index}`;
        const project = await api(page, 'POST', '/api/projects', { name });
        expect(project.name).toBe(name);
        expect(Number.isInteger(project.id)).toBe(true);
        ownedProjects.push(project.id);
      }
      const first = await api(page, 'GET', '/api/projects');
      expect(first.items).toHaveLength(100);
      // Creating through the UI must select the canonical new project even
      // after its list refresh starts again at the first page.
      await page.getByRole('button', { name: 'New project', exact: true }).last().click();
      const name = prefix + '-created';
      const modal = page.getByRole('dialog', { name: 'New project', exact: true });
      await modal.getByLabel('Project name').fill(name);
      const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/projects');
      await modal.getByRole('button', { name: 'Create project', exact: true }).click();
      const created = await (await createdResponse).json();
      expect(created.name).toBe(name);
      expect(Number.isInteger(created.id)).toBe(true);
      ownedProjects.push(created.id);
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Archive project', exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Archive project', exact: true }).click();
      const archiveDialog = page.getByRole('dialog', { name: 'Archive project?', exact: true });
      await archiveDialog.getByRole('button', { name: 'Archive project', exact: true }).click();
      await expect(archiveDialog).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Restore project', exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Restore project', exact: true }).click();
      const restoreDialog = page.getByRole('dialog', { name: 'Restore project?', exact: true });
      await restoreDialog.getByRole('button', { name: 'Restore project', exact: true }).click();
      await expect(restoreDialog).toHaveCount(0);
      await expect(page.getByRole('button', { name: 'Archive project', exact: true })).toBeVisible();
      await expect(page).not.toHaveURL(/view=archive/);
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      const selectedUrl = page.url();
      await page.goto(selectedUrl);
      await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
      expect(new URL(page.url()).searchParams.get('project')).toBe(String(created.id));
      await page.screenshot({ path: info.outputPath(`projects-pagination-metadata-${info.project.name}.png`), fullPage: true });
    } finally {
      for (const id of ownedProjects) await api(page, 'PATCH', `/api/projects/${id}`, { archived: true });
    }
  });
}
