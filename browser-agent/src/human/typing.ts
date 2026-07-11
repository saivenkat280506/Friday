import type { ElementHandle, Page } from "puppeteer";
import { randomBetween, shortPause } from "./wait.js";

export async function humanType(
  page: Page,
  element: ElementHandle<Element>,
  text: string,
): Promise<void> {
  await element.click({ clickCount: 3 });
  await shortPause(100, 250);
  await page.keyboard.press("Backspace");
  await shortPause(80, 160);

  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (Math.random() < 0.02 && i > 0) {
      const wrong = String.fromCharCode(char.charCodeAt(0) + 1);
      await page.keyboard.type(wrong, { delay: randomBetween(40, 90) });
      await shortPause(120, 280);
      await page.keyboard.press("Backspace");
      await shortPause(80, 150);
    }
    await page.keyboard.type(char, { delay: randomBetween(40, 180) });
    if (char === " ") {
      await shortPause(120, 320);
    }
  }
}