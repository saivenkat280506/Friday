import type { Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanType } from "../human/typing.js";
import { humanWait } from "../human/wait.js";

export async function playSpotify(page: Page, song: string): Promise<string> {
  const encoded = encodeURIComponent(song);
  await page.goto(`https://open.spotify.com/search/${encoded}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await humanWait(800, 1500);

  const firstTrack = await page.$("[data-testid='track-row'], [data-testid='tracklist-row']");
  if (firstTrack) {
    await humanClick(page, firstTrack);
    await humanWait(500, 1000);
  }

  const playBtn = await page.$(
    "[data-testid='play-button'], button[aria-label='Play'], button[aria-label*='Play']",
  );
  if (playBtn) {
    await humanClick(page, playBtn);
    await humanWait(400, 800);
    return `Playing ${song} on Spotify`;
  }

  const searchInput = await page.$("input[data-testid='search-input'], input[type='search']");
  if (searchInput) {
    await humanType(page, searchInput, song);
    await page.keyboard.press("Enter");
    await humanWait(1000, 1800);
    const row = await page.$("[data-testid='track-row']");
    if (row) await humanClick(page, row);
  }
  return `Opened Spotify search for ${song}`;
}