import type { Browser, Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanWait } from "../human/wait.js";

const POPUP_URL_HINTS = ["about:blank", "chrome-extension://"];

export async function bringMainToFront(page: Page): Promise<void> {
  await page.bringToFront();
}

export async function closeExtraTabs(
  browser: Browser,
  keep: Page,
  opts: { closeAll?: boolean } = {},
): Promise<number> {
  const pages = await browser.pages();
  let closed = 0;
  for (const tab of pages) {
    if (tab === keep) continue;
    const url = tab.url();
    if (opts.closeAll) {
      await tab.close().catch(() => undefined);
      closed += 1;
      continue;
    }
    if (POPUP_URL_HINTS.some((hint) => url.startsWith(hint))) {
      await tab.close().catch(() => undefined);
      closed += 1;
      continue;
    }
    if (url === "about:blank") {
      await tab.close().catch(() => undefined);
      closed += 1;
    }
  }
  await keep.bringToFront();
  return closed;
}

export async function dismissCommonPopups(page: Page): Promise<void> {
  const selectors = [
    "button#L2AGLb",
    "button[aria-label='Accept all']",
    "button[aria-label='Reject all']",
    "button[data-testid='cookie-banner-accept']",
    "button[aria-label='Close']",
  ];
  for (const sel of selectors) {
    try {
      const btn = await page.$(sel);
      if (btn) {
        await humanClick(page, btn);
        await humanWait(300, 700);
        break;
      }
    } catch {
      /* ignore */
    }
  }
}

export async function switchToNewTabIfNeeded(
  browser: Browser,
  main: Page,
  opts: { allowSwitch: boolean },
): Promise<Page> {
  const pages = await browser.pages();
  if (pages.length <= 1) return main;
  const newest = pages[pages.length - 1];
  if (newest === main) return main;
  if (!opts.allowSwitch) {
    await newest.close().catch(() => undefined);
    await main.bringToFront();
    return main;
  }
  await newest.bringToFront();
  return newest;
}