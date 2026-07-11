import type { Page } from "puppeteer";
import { randomBetween, shortPause } from "./wait.js";

export async function humanScroll(
  page: Page,
  direction: "up" | "down" = "down",
  amount = 3,
): Promise<void> {
  const sign = direction === "down" ? 1 : -1;
  const steps = Math.max(1, Math.min(amount, 8));
  for (let i = 0; i < steps; i++) {
    const delta = sign * Math.round(randomBetween(60, 140));
    await page.mouse.wheel({ deltaY: delta });
    await shortPause(60, 180);
  }
}