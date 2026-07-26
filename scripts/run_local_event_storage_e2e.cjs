const { chromium } = require("playwright");

async function main() {
  const pageUrl = process.argv[2] || "http://127.0.0.1:4174/test/index.html";
  const cdpUrl = process.argv[3] || "http://127.0.0.1:9230";
  const apiBaseUrl = process.argv[4];
  const browser = cdpUrl === "launch"
    ? await chromium.launch({ headless: true })
    : await chromium.connectOverCDP(cdpUrl);
  const context = cdpUrl === "launch"
    ? await browser.newContext()
    : browser.contexts()[0];
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  page.on("request", (request) => {
    if (request.url().includes("/chat/")) requests.push(request.url());
  });
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));

  try {
    await page.goto(pageUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    if (apiBaseUrl) await page.locator("#cfg-api-url").fill(apiBaseUrl);
    await page.getByRole("button", { name: "Start Chat" }).click();
    const input = page.getByPlaceholder("Type a message...");
    await input.waitFor({ timeout: 20000 });
    await input.fill("hello from local event storage e2e");
    await input.press("Enter");
    // Allow several 3-second polls plus at least one 5-second local tick. The
    // fixture duration is 45 seconds, keeping this check below one minute.
    await page.waitForTimeout(15000);

    const history = await page.evaluate(() => window.StimulizeChatroom.getHistory());
    const cursorPoll = requests.find((url) => {
      const after = new URL(url).searchParams.get("after");
      return url.includes("/chat/messages") && after && after !== "0";
    });
    const result = {
      ok: history.some((message) => message.content === "hello from local event storage e2e"),
      historyCount: history.length,
      roles: history.map((message) => message.role),
      aiMessageObserved: history.some((message) => message.role === "ai"),
      historyEndpointCalled: requests.some((url) => url.includes("/chat/history")),
      opaqueCursorPollObserved: Boolean(cursorPoll),
      browserErrors: errors,
    };
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok || !result.historyEndpointCalled || !result.opaqueCursorPollObserved) {
      process.exitCode = 1;
    }
  } finally {
    await page.close();
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
