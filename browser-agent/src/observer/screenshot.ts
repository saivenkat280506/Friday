import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Page } from "puppeteer";

function screenshotDir(): string {
  const base =
    process.env.FRIDAY_BROWSER_SCREENSHOTS ||
    path.join(os.homedir(), ".friday", "browser-screenshots");
  fs.mkdirSync(base, { recursive: true });
  return base;
}

export async function captureScreenshot(
  page: Page,
  label = "observe",
): Promise<{ path: string; base64: string }> {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filePath = path.join(screenshotDir(), `${label}-${stamp}.png`);
  const base64 = await page.screenshot({
    path: filePath,
    type: "png",
    encoding: "base64",
    fullPage: false,
  });
  return { path: filePath, base64 };
}