import { createCursor } from "ghost-cursor";
import type { ElementHandle, Page } from "puppeteer";
import { humanWait, shortPause } from "./wait.js";

export async function humanClick(
  page: Page,
  target: string | ElementHandle<Element>,
): Promise<void> {
  const cursor = createCursor(page);
  await humanWait(200, 600);
  if (typeof target === "string") {
    await cursor.click(target, { hesitate: 200, waitForClick: 80 });
    return;
  }
  const box = await target.boundingBox();
  if (!box) {
    throw new Error("Element not visible for human click");
  }
  const x = box.x + box.width * (0.3 + Math.random() * 0.4);
  const y = box.y + box.height * (0.3 + Math.random() * 0.4);
  await cursor.moveTo({ x, y });
  await shortPause(150, 400);
  await page.mouse.click(x, y);
}