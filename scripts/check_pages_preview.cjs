const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

async function main() {
  const url = process.env.CHECK_URL || process.argv[2];
  const screenshotPath = process.env.SCREENSHOT_PATH || process.argv[3];
  if (!url) {
    throw new Error('CHECK_URL is required');
  }

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  });
  let page;

  try {
    page = await browser.newPage({ viewport: { width: 1600, height: 1400 } });
    page.on('console', (msg) => {
      console.log(`[console:${msg.type()}] ${msg.text()}`);
    });
    page.on('pageerror', (error) => {
      console.log(`[pageerror] ${error.message}`);
    });

    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    const resultLocator = page.locator('#result');
    let title;
    let resultText;
    if (await resultLocator.count()) {
      await page.waitForFunction(() => {
        const pre = document.querySelector('#result');
        return !!pre && !String(pre.textContent || '').startsWith('Starting');
      }, { timeout: 180000 });
      title = await page.title();
      resultText = await resultLocator.innerText();
    } else {
      const frameHandle = await page.waitForSelector('iframe', { timeout: 30000 });
      const frame = await frameHandle.contentFrame();
      if (!frame) throw new Error('wrapper iframe contentFrame unavailable');
      await frame.waitForFunction(() => {
        const pre = document.querySelector('#result');
        return !!pre && !String(pre.textContent || '').startsWith('Starting');
      }, { timeout: 180000 });
      title = await frame.title();
      resultText = await frame.locator('#result').innerText();
    }
    console.log(`TITLE=${title}`);
    console.log(`RESULT<<EOF\n${resultText}\nEOF`);

    if (screenshotPath) {
      fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
      await page.screenshot({ path: screenshotPath, fullPage: true });
      console.log(`SCREENSHOT=${screenshotPath}`);
    }
  } catch (error) {
    if (screenshotPath) {
      try {
        fs.mkdirSync(path.dirname(screenshotPath), { recursive: true });
        await page.screenshot({ path: screenshotPath, fullPage: true });
        console.log(`SCREENSHOT=${screenshotPath}`);
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
