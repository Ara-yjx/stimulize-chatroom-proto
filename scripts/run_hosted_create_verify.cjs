const { chromium } = require('playwright');
const fs = require('fs');

const MANAGEMENT_API_URL = 'https://9wr63is7x6.execute-api.us-east-2.amazonaws.com/live';

function browserLaunchOptions() {
  const executablePath = process.env.PLAYWRIGHT_CHROME_PATH;
  if (executablePath) {
    return { executablePath, headless: true };
  }
  return { channel: 'chrome', headless: true };
}

function parseAccountFile(filePath) {
  const text = fs.readFileSync(filePath, 'utf8');
  const lines = text.split(/\r?\n/).map((line) => line.trim());
  let username = '';
  let password = '';
  for (let i = 0; i < lines.length; i += 1) {
    if (lines[i] === 'Username') username = lines[i + 1] || '';
    if (lines[i] === 'Password') password = lines[i + 1] || '';
  }
  if (!username || !password) {
    throw new Error(`Could not parse username/password from ${filePath}`);
  }
  return { username, password };
}

async function loginForToken(username, password) {
  const response = await fetch(`${MANAGEMENT_API_URL}/api/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Management login failed (${response.status})`);
  }
  const token = payload?.data?.access_token || '';
  if (!token) throw new Error('Management login did not return an access token');
  return token;
}

async function apiJson(path, token, init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set('Authorization', token);
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${MANAGEMENT_API_URL}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `API request failed (${response.status})`);
  }
  return payload?.data?.chatroom ?? payload?.data?.chatrooms ?? payload?.data ?? payload;
}

async function main() {
  const url = process.argv[2];
  const accountFile = process.argv[3];
  if (!url || !accountFile) {
    throw new Error('usage: node run_hosted_create_verify.cjs <hosted-url> <account-file>');
  }
  const { username, password } = parseAccountFile(accountFile);
  const token = await loginForToken(username, password);
  const uniqueName = `Hosted Create Verify ${Date.now().toString(36)}`;

  const browser = await chromium.launch(browserLaunchOptions());
  const context = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
  await context.addInitScript(
    ({ tokenValue, usernameValue }) => {
      window.sessionStorage.setItem('stimulize.editor.managementToken', tokenValue);
      window.sessionStorage.setItem('stimulize.editor.managementUsername', usernameValue);
    },
    { tokenValue: token, usernameValue: username }
  );

  let createdChatroom = null;
  try {
    const page = await context.newPage();
    page.on('console', (msg) => console.log(`[console:${msg.type()}] ${msg.text()}`));
    page.on('pageerror', (error) => console.log(`[pageerror] ${error.message}`));

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.getByRole('button', { name: 'Logout' }).waitFor({ timeout: 60000 });
    await page.getByRole('button', { name: 'Create Chatroom' }).click();
    const input = page.getByPlaceholder('Chatroom name');
    await input.fill(uniqueName);
    await input.press('Enter');
    await page.getByText(uniqueName, { exact: true }).waitFor({ timeout: 60000 });
    await page.getByText(uniqueName, { exact: true }).click();
    await page.getByRole('heading', { name: 'Edit Chatroom' }).waitFor({ timeout: 60000 });

    const pageText = await page.textContent('body');
    const hasMaxDurationLabel = pageText.includes('Max Duration (sec)');

    const chatrooms = await apiJson('/api/getChatrooms', token, { method: 'POST' });
    createdChatroom = chatrooms.find((chatroom) => chatroom.name === uniqueName);
    if (!createdChatroom) {
      throw new Error('created chatroom not found in management API list');
    }
    const detail = await apiJson(`/api/getChatroom/${createdChatroom.id}`, token, { method: 'POST' });

    const result = {
      ok: true,
      chatroomId: createdChatroom.id,
      browser: {
        hasMaxDurationLabel,
      },
      setting: {
        simulate_pairing_seconds: detail.setting.simulate_pairing_seconds,
        timer_min_minutes: detail.setting.timer_min_minutes,
        timer_max_minutes: detail.setting.timer_max_minutes,
        max_duration_seconds: detail.setting.max_duration_seconds,
      },
    };
    console.log(JSON.stringify(result, null, 2));

    await apiJson(`/api/deleteChatroom/${createdChatroom.id}`, token, {
      method: 'POST',
    });
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
