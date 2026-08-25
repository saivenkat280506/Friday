import { createCursor } from "ghost-cursor";
import type { ElementHandle, Page } from "puppeteer";
import { humanWait, shortPause } from "./wait.js";

export async function humanClick(
  page: Page,
  target: string | ElementHandle<Element>,
): Promise<void> {
  const cursor = createCursor(page);
  await humanWait(200, 600);
  await cursor.click(target, { hesitate: 200, waitForClick: 80 });
}

export async function humanDrag(
  page: Page,
  start: { x: number; y: number },
  end: { x: number; y: number },
): Promise<void> {
  const cursor = createCursor(page);
  await humanWait(200, 500);
  await cursor.moveTo(start);
  await shortPause(120, 280);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  const steps = 8 + Math.floor(Math.random() * 6);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const jitterX = randomBetween(-2, 2);
    const jitterY = randomBetween(-2, 2);
    const x = start.x + (end.x - start.x) * t + jitterX;
    const y = start.y + (end.y - start.y) * t + jitterY;
    await page.mouse.move(x, y);
    await shortPause(20, 60);
  }
  await page.mouse.up();
  await shortPause(150, 350);
}

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}