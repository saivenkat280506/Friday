import type { Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanType } from "../human/typing.js";
import { humanWait } from "../human/wait.js";
import { dismissCommonPopups } from "../session/tabs.js";

async function findPromptBox(page: Page) {
  const selectors = [
    "#prompt-textarea",
    "textarea[placeholder*='Message']",
    "textarea[data-id='root']",
    "div[contenteditable='true'][id='prompt-textarea']",
    "div[role='textbox']",
  ];
  for (const sel of selectors) {
    const el = await page.$(sel);
    if (el) return el;
  }
  return null;
}

export async function sendChatGptPrompt(
  page: Page,
  prompt: string,
): Promise<{ message: string; responseText: string }> {
  await page.goto("https://chatgpt.com/", {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  });
  await humanWait(800, 1600);
  await dismissCommonPopups(page);

  const loginHint = await page.$("button[data-testid='login-button'], a[href*='login']");
  if (loginHint) {
    return {
      message: "ChatGPT requires login — open headed browser and sign in once (persistent profile).",
      responseText: "",
    };
  }

  const box = await findPromptBox(page);
  if (!box) {
    throw new Error("ChatGPT prompt box not found — UI may have changed or login required.");
  }

  await humanClick(page, box);
  await humanType(page, box, prompt);
  await humanWait(400, 900);

  const sendBtn = await page.$(
    "button[data-testid='send-button'], button[aria-label='Send prompt'], button[aria-label='Send message']",
  );
  if (sendBtn) {
    await humanClick(page, sendBtn);
  } else {
    await page.keyboard.press("Enter");
  }

  await humanWait(2500, 4000);
  await page.waitForNetworkIdle({ idleTime: 1200, timeout: 60000 }).catch(() => undefined);

  const responseText = await page.evaluate(() => {
    const nodes = document.querySelectorAll(
      "[data-message-author-role='assistant'], .markdown, [class*='agent-turn']",
    );
    const last = nodes[nodes.length - 1] as HTMLElement | undefined;
    return (last?.innerText || "").trim().slice(0, 4000);
  });

  if (!responseText) {
    return {
      message: "Prompt sent to ChatGPT — waiting for response (may still be generating).",
      responseText: "",
    };
  }

  return {
    message: `ChatGPT responded (${responseText.length} chars).`,
    responseText,
  };
}