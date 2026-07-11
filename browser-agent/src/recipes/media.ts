import type { Browser, Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanWait } from "../human/wait.js";
import { observePage } from "../observer/dom-state.js";

async function findMediaPage(browser: Browser): Promise<Page | null> {
  const pages = await browser.pages();
  for (const p of pages.reverse()) {
    const url = p.url();
    if (
      url.includes("open.spotify.com") ||
      url.includes("youtube.com") ||
      url.includes("music.youtube.com")
    ) {
      return p;
    }
  }
  return pages[pages.length - 1] || null;
}

export async function mediaControl(
  browser: Browser,
  current: Page,
  action: string,
): Promise<string> {
  const page = (await findMediaPage(browser)) || current;
  await page.bringToFront();
  await humanWait(300, 700);

  const state = await observePage(page, browser);
  const keyMap: Record<string, string> = {
    play: " ",
    pause: " ",
    next: "MediaTrackNext",
    prev: "MediaTrackPrevious",
  };

  if (state.media.platform === "spotify") {
    const selectors: Record<string, string> = {
      play: "[data-testid='control-button-play'], button[aria-label='Play']",
      pause: "[data-testid='control-button-pause'], button[aria-label='Pause']",
      next: "[data-testid='control-button-skip-forward'], button[aria-label='Next']",
      prev: "[data-testid='control-button-skip-back'], button[aria-label='Previous']",
    };
    const sel = selectors[action];
    if (sel) {
      const btn = await page.$(sel);
      if (btn) {
        await humanClick(page, btn);
        return `Spotify ${action} triggered`;
      }
    }
  }

  const key = keyMap[action];
  if (key) {
    await page.keyboard.press(key as Parameters<typeof page.keyboard.press>[0]);
    return `Media ${action} sent via keyboard`;
  }
  throw new Error(`Unknown media action: ${action}`);
}