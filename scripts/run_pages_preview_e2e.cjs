const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const MANAGEMENT_API_URL = 'https://9wr63is7x6.execute-api.us-east-2.amazonaws.com/live';
const TOKEN_STORAGE_KEY = 'stimulize.editor.managementToken';
const USERNAME_STORAGE_KEY = 'stimulize.editor.managementUsername';

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

async function waitForAiConversation(page, timeoutMs = 120000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const result = await page.evaluate(() => {
      const frames = Array.from(document.querySelectorAll('iframe'));
      const previews = frames.filter((frame) => {
        const src = frame.getAttribute('src') || '';
        return src.startsWith('blob:');
      });
      const histories = previews.map((frame) => {
        try {
          const api = frame.contentWindow && frame.contentWindow.StimulizeChatroom;
          return api && typeof api.getHistory === 'function' ? api.getHistory() : [];
        } catch {
          return [];
        }
      });
      const merged = histories.flat();
      const aiMessages = merged.filter((msg) => msg && msg.role === 'ai');
      const aiSenders = [...new Set(aiMessages.map((msg) => msg.sender).filter(Boolean))];
      return {
        previewCount: previews.length,
        historyCounts: histories.map((h) => h.length),
        aiMessages: aiMessages.map((m) => ({ sender: m.sender, content: m.content })),
        aiSenders,
      };
    });
    if (result.previewCount >= 2 && result.aiMessages.length >= 2 && result.aiSenders.length >= 2) {
      return result;
    }
    await page.waitForTimeout(1000);
  }
  throw new Error('Timed out waiting for AI-to-AI conversation');
}

async function startAllPreviewFrames(page, timeoutMs = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const result = await page.evaluate(() => {
      const frames = Array.from(document.querySelectorAll('iframe')).filter((frame) => {
        const src = frame.getAttribute('src') || '';
        return src.startsWith('blob:');
      });
      let ready = 0;
      for (const frame of frames) {
        try {
          const doc = frame.contentDocument;
          const win = frame.contentWindow;
          if (!doc || !win) continue;
          const startButton = doc.querySelector('.stim-beta-start');
          if (startButton) {
            startButton.click();
            continue;
          }
          if (win.StimulizeChatroom) {
            ready += 1;
          }
        } catch {}
      }
      return { frameCount: frames.length, ready };
    });
    if (result.frameCount >= 2 && result.ready >= 2) {
      return;
    }
    await page.waitForTimeout(1000);
  }
  throw new Error('Timed out waiting for preview frames to start');
}

async function main() {
  const detailUrl = process.argv[2];
  const accountFile = process.argv[3];
  const screenshotPath = process.argv[4];
  if (!detailUrl || !accountFile) {
    throw new Error('usage: node run_pages_preview_e2e.cjs <url> <account-file> [screenshot]');
  }
  const { username, password } = parseAccountFile(accountFile);
  const token = await loginForToken(username, password);

  const browser = await chromium.launch(browserLaunchOptions());
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1400 } });
    await context.addInitScript(
      ({ tokenValue, usernameValue }) => {
        window.sessionStorage.setItem('stimulize.editor.managementToken', tokenValue);
        window.sessionStorage.setItem('stimulize.editor.managementUsername', usernameValue);
      },
      { tokenValue: token, usernameValue: username }
    );
    page = await context.newPage();
    page.on('console', (msg) => console.log(`[console:${msg.type()}] ${msg.text()}`));
    page.on('pageerror', (error) => console.log(`[pageerror] ${error.message}`));

    await page.goto(detailUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.getByRole('button', { name: 'Logout' }).waitFor({ timeout: 60000 });
    await page.getByRole('heading', { name: 'Edit Chatroom' }).waitFor({ timeout: 60000 });

    await page.getByRole('button', { name: 'Save & Launch Preview' }).click();
    await page.waitForSelector('iframe[src^="blob:"]', { timeout: 60000 });

    await page.getByRole('button', { name: '+ Launch another preview' }).click();
    await page.waitForFunction(() => {
      return Array.from(document.querySelectorAll('iframe')).filter((frame) => {
        const src = frame.getAttribute('src') || '';
        return src.startsWith('blob:');
      }).length >= 2;
    }, { timeout: 60000 });

    await startAllPreviewFrames(page, 60000);
    const conversation = await waitForAiConversation(page, 120000);
    if (screenshotPath) {
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: true });
    }
    console.log(JSON.stringify({ ok: true, conversation }, null, 2));
  } catch (error) {
    if (page && screenshotPath) {
      try {
        fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
        await page.screenshot({ path: screenshotPath, fullPage: true });
      } catch {}
    }
    throw error;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
