const fs = require('fs');
const { chromium } = require('playwright');

const MANAGEMENT_API_URL = 'https://9wr63is7x6.execute-api.us-east-2.amazonaws.com/live';

function parseAccountFile(filePath) {
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/).map((line) => line.trim());
  const valueAfter = (label) => {
    const index = lines.indexOf(label);
    return index >= 0 ? lines[index + 1] || '' : '';
  };
  const username = valueAfter('Username');
  const password = valueAfter('Password');
  if (!username || !password) throw new Error('Account file is missing Username or Password');
  return { username, password };
}

async function apiJson(path, token, init = {}) {
  const headers = new Headers(init.headers || {});
  if (token) headers.set('Authorization', token);
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${MANAGEMENT_API_URL}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Management API failed (${response.status})`);
  return payload?.data?.chatroom ?? payload?.data ?? payload;
}

function asChatroomList(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.chatrooms) ? payload.chatrooms : [];
}

async function main() {
  const editorUrl = process.argv[2] || 'http://127.0.0.1:3000/#/chatroom';
  const accountFile = process.argv[3];
  const resultFile = process.argv[4] || '/tmp/stimulize-resume-editor-e2e.json';
  const screenshotPath = process.argv[5];
  if (!accountFile) throw new Error('usage: node run_resume_editor_e2e.cjs <editor-url> <account-file> [result-json] [screenshot]');

  const { username, password } = parseAccountFile(accountFile);
  fs.rmSync(resultFile, { force: true });
  const login = await apiJson('/api/login', '', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  const token = login.access_token;
  if (!token) throw new Error('Login did not return a token');

  const existingChatrooms = asChatroomList(
    await apiJson('/api/getChatrooms', token, { method: 'POST' }),
  );
  for (const chatroom of existingChatrooms) {
    if (chatroom.name?.startsWith('Resume Browser E2E ')) {
      await apiJson(`/api/deleteChatroom/${chatroom.id}`, token, { method: 'POST' });
    }
  }

  const browser = process.env.PLAYWRIGHT_CDP_URL
    ? await chromium.connectOverCDP(process.env.PLAYWRIGHT_CDP_URL)
    : await chromium.launch({
      ...(process.env.PLAYWRIGHT_CHROME_PATH
        ? { executablePath: process.env.PLAYWRIGHT_CHROME_PATH }
        : { channel: 'chrome' }),
      headless: true,
    });
  const context = process.env.PLAYWRIGHT_CDP_URL
    ? browser.contexts()[0]
    : await browser.newContext({ viewport: { width: 1500, height: 1200 } });
  await context.addInitScript(({ tokenValue, usernameValue }) => {
    const tokenCreatedAt = Date.now();
    localStorage.setItem('stimulize.editor.managementAuth', JSON.stringify({
      token: tokenValue,
      username: usernameValue,
      tokenCreatedAt,
      tokenExpiresAt: tokenCreatedAt + (3 * 60 * 60 * 1000),
    }));
  }, { tokenValue: token, usernameValue: username });

  const page = await context.newPage();
  const errors = [];
  let createdChatroom = null;
  let runtimeAuth = null;
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('response', async (response) => {
    if (!response.url().endsWith('/auth/token') || !response.ok()) return;
    runtimeAuth = await response.json().catch(() => null);
  });

  try {
    const name = `Resume Browser E2E ${Date.now().toString(36)}`;
    await page.goto(editorUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.getByRole('button', { name: 'Create Chatroom' }).waitFor({ timeout: 60000 });
    await page.getByRole('button', { name: 'Create Chatroom' }).click();
    const nameInput = page.getByPlaceholder('Chatroom name');
    await nameInput.fill(name);
    await nameInput.press('Enter');
    await page.getByText(name, { exact: true }).waitFor({ timeout: 60000 });

    const chatrooms = asChatroomList(
      await apiJson('/api/getChatrooms', token, { method: 'POST' }),
    );
    createdChatroom = chatrooms.find((chatroom) => chatroom.name === name);
    if (!createdChatroom) throw new Error('Created chatroom not found through management API');

    await page.getByText(name, { exact: true }).click();
    await page.getByRole('heading', { name: 'Edit Chatroom' }).waitFor({ timeout: 60000 });

    await page.getByRole('switch', { name: 'Mimic human', exact: true }).click();
    await page.getByRole('switch', { name: 'Resume conversation', exact: true }).click();

    await page.getByRole('button', { name: 'Save, Activate, and Launch Preview' }).click();
    const frameElement = await page.waitForSelector('iframe[src^="blob:"]', { timeout: 60000 });
    const frame = await frameElement.contentFrame();
    if (!frame) throw new Error('Preview iframe is unavailable');
    await frame.getByRole('button', { name: 'Start Chat' }).click();
    const input = frame.getByPlaceholder('Type a message...');
    await input.waitFor({ timeout: 60000 });
    await input.fill('I learn best by building small examples.');
    await input.press('Enter');

    await frame.waitForFunction(() => {
      const history = window.StimulizeChatroom?.getHistory?.() || [];
      return history.some((message) => message.role === 'ai');
    }, null, { timeout: 45000 });
    const history = await frame.evaluate(() => window.StimulizeChatroom.getHistory());
    if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true });
    if (!runtimeAuth?.conversation_id) throw new Error('Runtime auth response was not captured');

    const result = {
      ok: true,
      chatroom_id: createdChatroom.id,
      conversation_id: runtimeAuth.conversation_id,
      participant_id: runtimeAuth.participant_id,
      episode_number: runtimeAuth.episode_number,
      ai_message_count: history.filter((message) => message.role === 'ai').length,
      browser_errors: errors,
    };
    fs.writeFileSync(resultFile, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true }).catch(() => {});
    fs.writeFileSync(resultFile, JSON.stringify({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      browser_errors: errors,
    }, null, 2));
    throw error;
  } finally {
    if (createdChatroom) {
      await apiJson(`/api/deleteChatroom/${createdChatroom.id}`, token, { method: 'POST' })
        .catch((error) => console.error(`Chatroom cleanup failed: ${error.message}`));
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
