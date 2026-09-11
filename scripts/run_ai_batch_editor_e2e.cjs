const fs = require('fs');
const { chromium } = require('playwright');

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

async function apiJson(baseUrl, path, token, init = {}) {
  const headers = new Headers(init.headers || {});
  if (token) headers.set('Authorization', token);
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Management API failed (${response.status})`);
  return payload.data ?? payload;
}

function formInput(page, label) {
  return page.locator('.arco-form-item').filter({ hasText: label }).locator('input').first();
}

function formSwitch(page, label) {
  return page.locator('.arco-form-item').filter({ hasText: label }).getByRole('switch').first();
}

async function main() {
  const editorUrl = process.argv[2] || 'http://127.0.0.1:4174/#/chatroom';
  const managementUrl = process.argv[3] || 'http://127.0.0.1:5002';
  const accountFile = process.argv[4];
  const resultFile = process.argv[5] || '/tmp/stimulize-ai-batch-editor-e2e.json';
  const screenshotPath = process.argv[6] || '/tmp/stimulize-ai-batch-editor-e2e.png';
  if (!accountFile) {
    throw new Error(
      'usage: node run_ai_batch_editor_e2e.cjs <editor-url> <management-url> <account-file> [result-json] [screenshot]',
    );
  }

  const { username, password } = parseAccountFile(accountFile);
  const login = await apiJson(managementUrl, '/api/login', '', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  const token = login.access_token;
  if (!token) throw new Error('Login did not return a token');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1500, height: 1200 } });
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
  const browserErrors = [];
  let createdChatroom;
  let batchPage;
  for (const target of [page]) {
    target.on('pageerror', (error) => browserErrors.push(error.message));
    target.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
  }

  try {
    const name = `AI Batch Browser E2E ${Date.now().toString(36)}`;
    await page.goto(editorUrl, { waitUntil: 'networkidle', timeout: 60000 });
    await page.getByRole('button', { name: 'Create Chatroom' }).click();
    await page.getByPlaceholder('Chatroom name').fill(name);
    await page.getByPlaceholder('Chatroom name').press('Enter');
    await page.getByText(name, { exact: true }).waitFor({ timeout: 60000 });

    const list = await apiJson(managementUrl, '/api/getChatrooms', token, { method: 'POST' });
    createdChatroom = list.chatrooms.find((chatroom) => chatroom.name === name);
    if (!createdChatroom) throw new Error('UI-created chatroom was not returned by management API');

    await page.getByText(name, { exact: true }).click();
    await page.getByRole('heading', { name: 'Edit Chatroom' }).waitFor({ timeout: 60000 });
    await formSwitch(page, 'AI-only mode').click();
    await page.getByText('Start Conversation', { exact: true }).waitFor();

    await formInput(page, 'Max message length').fill('80');
    await formInput(page, 'Conversation length').fill('200');
    await formInput(page, 'Max turns').fill('2');
    const topicItem = page.locator('.arco-form-item').filter({ hasText: 'Chatroom topic' });
    await topicItem.locator('textarea').fill('Discuss one small way to improve a study routine.');
    await page.screenshot({ path: screenshotPath, fullPage: true });

    const popupPromise = context.waitForEvent('page');
    await page.getByRole('button', { name: 'Start once' }).click();
    batchPage = await popupPromise;
    await page.waitForTimeout(1000);
    if (batchPage.isClosed()) {
      const formErrors = await page.locator('.arco-form-message').allInnerTexts();
      const notices = await page.locator('.arco-message').allInnerTexts();
      throw new Error(
        `Start once closed before launch; formErrors=${JSON.stringify(formErrors)} notices=${JSON.stringify(notices)}`,
      );
    }
    batchPage.on('pageerror', (error) => browserErrors.push(error.message));
    batchPage.on('console', (message) => {
      if (message.type() === 'error') browserErrors.push(message.text());
    });
    await batchPage.waitForURL(/\/ai-batches\//, { timeout: 60000 });
    await batchPage.getByRole('heading', { name: 'AI Conversation Batch' }).waitFor({ timeout: 60000 });
    await batchPage.getByText('completed', { exact: true }).first().waitFor({ timeout: 120000 });

    const batchId = batchPage.url().split('/ai-batches/')[1];
    const detail = await apiJson(
      managementUrl,
      `/api/getAiConversationBatch/${batchId}`,
      token,
      { method: 'POST', body: JSON.stringify({ offset: 0, limit: 1 }) },
    );
    const conversation = detail.batch.conversations[0];
    const history = await apiJson(
      managementUrl,
      `/api/getAiConversationHistory/${conversation.conversation_id}`,
      token,
      { method: 'POST', body: JSON.stringify({ limit: 20 }) },
    );
    const messages = history.events.filter((event) => event.type === 'message');
    if (detail.batch.status !== 'completed' || messages.length !== 2) {
      throw new Error(`Expected completed two-turn conversation, got ${detail.batch.status}/${messages.length}`);
    }
    const bodyText = await batchPage.locator('body').innerText();
    for (const message of messages) {
      if (!bodyText.includes(message.content)) {
        throw new Error('Completed history was not visible on the batch page');
      }
    }
    await batchPage.screenshot({ path: screenshotPath, fullPage: true });

    const result = {
      ok: true,
      chatroom_id: createdChatroom.id,
      batch_job_id: detail.batch.batch_job_id,
      conversation_id: conversation.conversation_id,
      batch_status: detail.batch.status,
      message_count: messages.length,
      browser_errors: browserErrors,
    };
    fs.writeFileSync(resultFile, JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    fs.writeFileSync(resultFile, JSON.stringify({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      browser_errors: browserErrors,
    }, null, 2));
    throw error;
  } finally {
    if (createdChatroom) {
      await apiJson(
        managementUrl,
        `/api/deleteChatroom/${createdChatroom.id}`,
        token,
        { method: 'POST' },
      ).catch((error) => console.error(`Chatroom cleanup failed: ${error.message}`));
    }
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
