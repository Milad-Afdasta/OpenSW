// Optional real released static client against the current, unmodified API.
// Build the named Git release separately; never replace or intercept API data.
const fs = require('node:fs/promises');
const path = require('node:path');

async function releasedUI(page, app, origin) {
  if (!process.env.OPENSW_RELEASED_UI_DIR) return;
  const root = path.resolve(process.env.OPENSW_RELEASED_UI_DIR, app);
  await fs.access(path.join(root, 'index.html'));
  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== new URL(origin).origin || request.method() !== 'GET' || url.pathname.startsWith('/api/')) {
      return route.continue();
    }
    const relative = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(root, relative);
    if (!file.startsWith(root + path.sep)) throw new Error('Released static asset escaped its app directory.');
    await fs.access(file);
    return route.fulfill({ path: file });
  });
}

module.exports = { releasedUI };
