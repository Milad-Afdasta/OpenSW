const { test, expect } = require('../../web/node_modules/@playwright/test');
const fs = require('node:fs/promises');
const fsSync = require('node:fs');
const { releasedUI } = require('./released-ui.cjs');

const credentialPath = process.env.OPENSW_QA_CONFIG;
if (!credentialPath) throw new Error('Set OPENSW_QA_CONFIG to a private JSON file containing the selected apps’ url, username, and password. See tests/browser/README.md.');
const configuration = JSON.parse(fsSync.readFileSync(credentialPath, 'utf8'));
const selected = process.env.OPENSW_QA_APP ? [process.env.OPENSW_QA_APP] : ['files', 'projects', 'notes', 'chat'];
if (selected.some(app => !['files', 'projects', 'notes', 'chat'].includes(app))) throw new Error('OPENSW_QA_APP must be files, projects, notes, or chat.');

async function api(page, method, path, payload, expected = [200, 201]) {
  const result = await page.evaluate(async ({ method, path, payload }) => {
    const sessionResponse = await fetch('/api/me');
    const session = await sessionResponse.json();
    const response = await fetch(path, {
      method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf_token },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    return { status: response.status, body: await response.json() };
  }, { method, path, payload });
  expect(expected, `${method} ${path}: ${result.body.detail || 'unexpected status'}`).toContain(result.status);
  return result.body;
}

async function login(page, app, project) {
  const config = configuration[app];
  if (!config || !config.url || !config.username || !config.password) throw new Error(`Missing private browser configuration for ${app}.`);
  await releasedUI(page, app, config.url);
  await page.addInitScript(theme => localStorage.setItem('OpenSW-theme', theme), project.use.colorScheme);
  await page.goto(config.url);
  try {
    await page.getByLabel('Username', { exact: true }).fill(config.username);
    await page.getByLabel('Password', { exact: true }).fill(config.password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Open account menu' })).toBeVisible();
  } catch {
    // Playwright's failure context can contain input values even without a trace.
    const password = page.getByLabel('Password', { exact: true });
    if (await password.isVisible().catch(() => false)) await password.fill('').catch(() => {});
    throw new Error(`${app}: sign-in did not reach the app. Check credentials, server logs and the one-minute sign-in rate window. Credential field cleared before capture.`);
  }
  await expect(page.locator('html')).toHaveAttribute('data-theme', project.use.colorScheme);
}

async function revealNavigation(page, locator) {
  if (!await locator.isVisible()) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  }
  await expect(locator).toBeVisible();
}

async function assertLayoutAndCapture(page, info, app) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow, 'The document must not overflow horizontally.').toBe(false);
  await page.screenshot({ path: info.outputPath(`${app}-${info.project.name}.png`), fullPage: true });
}

for (const app of selected) {
  test(`${app}: a complete everyday workflow confirms persisted state`, async ({ page }, info) => {
    await login(page, app, info.project);
    const prefix = `browser-${info.project.name}-${Date.now().toString(36)}`;
    if (app === 'files') {
      await page.getByRole('button', { name: 'New folder', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: 'New folder', exact: true });
      await dialog.getByLabel('Folder name').fill(prefix);
      await dialog.getByRole('button', { name: 'Create folder', exact: true }).click();
      const folderButton = page.getByRole('button', { name: prefix, exact: false }).filter({ has: page.locator('.file-type-icon.folder') });
      await expect(folderButton).toBeVisible();
      await folderButton.click();
      await expect(page.getByRole('heading', { name: prefix, exact: true })).toBeVisible();
      await expect(page).toHaveURL(/folder=\d+/);
      const content = Buffer.from('OpenSW browser check: original file bytes.\n');
      const filename = `${prefix}.txt`;
      await page.getByLabel('Choose files to upload').setInputFiles({ name: filename, mimeType: 'text/plain', buffer: content });
      await expect(page.getByRole('button', { name: `Download ${filename}`, exact: true })).toBeVisible();
      const downloadWait = page.waitForEvent('download');
      await page.getByRole('button', { name: `Download ${filename}`, exact: true }).click();
      const download = await downloadWait;
      expect(await fs.readFile(await download.path())).toEqual(content);
      await page.getByRole('button', { name: `Rename ${filename}`, exact: true }).click();
      const renamed = `${prefix}-renamed.txt`;
      await page.getByRole('dialog', { name: 'Rename item' }).getByLabel('Name', { exact: true }).fill(renamed);
      await page.getByRole('button', { name: 'Save name', exact: true }).click();
      await expect(page.getByRole('button', { name: `Download ${renamed}`, exact: true })).toBeVisible();
      const folder = Number(new URL(page.url()).searchParams.get('folder'));
      const listing = await api(page, 'GET', `/api/files?parent_id=${folder}`);
      const file = listing.items.find(item => item.name === renamed);
      expect(file).toBeTruthy();
      await assertLayoutAndCapture(page, info, app);
      await api(page, 'DELETE', `/api/files/${file.id}`);
      await api(page, 'DELETE', `/api/files/${folder}`);
    } else if (app === 'projects') {
      await page.getByRole('button', { name: 'New project', exact: true }).last().click();
      const dialog = page.getByRole('dialog', { name: 'New project', exact: true });
      await dialog.getByLabel('Project name').fill(prefix);
      await dialog.getByLabel('Description', { exact: true }).fill('A focused browser verification project.');
      await dialog.getByRole('button', { name: 'Create project', exact: true }).click();
      await page.getByRole('button', { name: 'New task', exact: true }).click();
      const create = page.getByRole('dialog', { name: 'New task', exact: true });
      await create.getByLabel('Task title').fill(prefix + ' task');
      await create.getByRole('button', { name: 'Create task', exact: true }).click();
      const details = page.getByRole('dialog', { name: 'Task details' });
      await expect(details.getByLabel('Task title')).toHaveValue(prefix + ' task');
      await details.getByLabel('Description', { exact: true }).fill('A real saved task description.');
      await details.getByLabel('Status', { exact: true }).selectOption('doing');
      await details.getByLabel('Priority', { exact: true }).selectOption('high');
      await details.getByLabel('Due date', { exact: true }).fill('2030-01-15');
      await details.getByRole('button', { name: 'Save changes', exact: true }).click();
      await expect(details.getByRole('button', { name: 'Save changes', exact: true })).toBeDisabled();
      await details.getByLabel('Write a comment').fill('The browser workflow is confirmed.');
      await details.getByRole('button', { name: 'Add comment', exact: true }).click();
      await expect(details.getByText('The browser workflow is confirmed.', { exact: true })).toBeVisible();
      const taskId = Number(new URL(page.url()).searchParams.get('task'));
      const task = await api(page, 'GET', `/api/tasks/${taskId}`);
      expect(task.status).toBe('doing');
      expect(task.priority).toBe('high');
      expect(task.due_date).toBe('2030-01-15');
      expect(task.description).toBe('A real saved task description.');
      await details.getByRole('button', { name: 'Close', exact: true }).click();
      await expect(details).toHaveCount(0);
      await assertLayoutAndCapture(page, info, app);
      await api(page, 'PATCH', `/api/tasks/${task.id}`, { archived: true, expected_revision: task.revision });
      await api(page, 'PATCH', `/api/projects/${task.project_id}`, { archived: true });
    } else if (app === 'notes') {
      await page.getByRole('button', { name: 'New page', exact: true }).last().click();
      const dialog = page.getByRole('dialog', { name: 'New page', exact: true });
      await dialog.getByLabel('Page title').fill(prefix);
      await dialog.getByRole('button', { name: 'Create page', exact: true }).click();
      const editor = page.getByLabel('Page content', { exact: true });
      const text = '# A clear note\n\nSaved through the actual browser interface.';
      await editor.fill(text);
      await page.getByRole('button', { name: 'Preview', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'A clear note', exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Edit', exact: true }).click();
      await expect(editor).toHaveValue(text);
      await page.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
      const noteId = Number(new URL(page.url()).searchParams.get('page'));
      const note = await api(page, 'GET', `/api/notes/${noteId}`);
      expect(note.body).toBe(text);
      await page.reload();
      await expect(editor).toHaveValue(text);
      await assertLayoutAndCapture(page, info, app);
      await api(page, 'PATCH', `/api/notes/${noteId}`, { archived: true, expected_revision: note.revision });
    } else {
      const newChannel = page.getByRole('button', { name: 'New channel', exact: true });
      await revealNavigation(page, newChannel);
      await newChannel.click();
      const dialog = page.getByRole('dialog', { name: 'New channel', exact: true });
      await dialog.getByLabel('Channel name').fill(prefix);
      await dialog.getByRole('button', { name: 'Create channel', exact: true }).click();
      const message = prefix + ' message';
      await page.getByLabel('Message', { exact: true }).fill(message);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await expect(page.getByText(message, { exact: true })).toBeVisible();
      const conversation = Number(new URL(page.url()).searchParams.get('conversation'));
      const history = await api(page, 'GET', `/api/chat/conversations/${conversation}/messages`);
      const stored = history.items.find(item => item.body === message);
      expect(stored).toBeTruthy();
      await page.getByRole('button', { name: `Edit message ${stored.id}`, exact: true }).click();
      await page.getByRole('dialog', { name: 'Edit message', exact: true }).getByLabel('Message text').fill(message + ' edited');
      await page.getByRole('button', { name: 'Save message', exact: true }).click();
      await expect(page.getByText(message + ' edited', { exact: true })).toBeVisible();
      await assertLayoutAndCapture(page, info, app);
      await page.getByRole('button', { name: `Delete message ${stored.id}`, exact: true }).click();
      await page.getByRole('dialog', { name: 'Delete message?', exact: true }).getByRole('button', { name: 'Delete message', exact: true }).click();
      await expect(page.getByText('This message was deleted.', { exact: true })).toBeVisible();
      const final = await api(page, 'GET', `/api/chat/conversations/${conversation}/messages`);
      expect(final.items.find(item => item.id === stored.id).deleted).toBe(true);
    }
  });
}
