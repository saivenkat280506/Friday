import type { Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanWait } from "../human/wait.js";

export async function playYouTube(page: Page, song: string, music = false): Promise<string> {
  const base = music ? "https://music.youtube.com" : "https://www.youtube.com";
  const query = encodeURIComponent(song);
  await page.goto(`${base}/results?search_query=${query}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await humanWait(1000, 1800);

  const firstVideo = await page.$(
    "ytd-video-renderer a#video-title, a.yt-simple-endpoint.style-scope.yt-formatted-string",
  );
  if (!firstVideo) throw new Error("No YouTube results found");
  await humanClick(page, firstVideo);
  await page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => undefined);
  await humanWait(1200, 2000);

  const playButton = await page.$(".ytp-play-button, button[aria-label='Play']");
  if (playButton) {
    const label = await playButton.evaluate((el) => el.getAttribute("aria-label") || "");
    if (label.toLowerCase().includes("play")) {
      await humanClick(page, playButton);
    }
  } else {
    await page.keyboard.press("k");
  }
  const platform = music ? "YouTube Music" : "YouTube";
  return `Playing ${song} on ${platform}`;
}