import type { Page } from "puppeteer";
import { humanClick, humanDrag } from "../human/cursor.js";
import { humanType } from "../human/typing.js";
import { humanWait } from "../human/wait.js";

function parseSongQuery(song: string): { title: string; artist: string } {
  const parts = song.split(/\s+[-–—by]+\s+/i);
  if (parts.length >= 2) {
    return { title: parts[0].trim(), artist: parts.slice(1).join(" ").trim() };
  }
  return { title: song.trim(), artist: "" };
}

async function pickBestTrack(
  page: Page,
  title: string,
  artist: string,
): Promise<{ index: number; score: number } | null> {
  return page.evaluate(
    (wantTitle, wantArtist) => {
      const rows = Array.from(
        document.querySelectorAll("[data-testid='track-row'], [data-testid='tracklist-row']"),
      );
      let best: { index: number; score: number } | null = null;
      rows.forEach((row, index) => {
        const titleEl = row.querySelector(
          "[data-testid='internal-track-link'], a[href*='/track/'], div[dir='auto']",
        );
        const artistEl = row.querySelector(
          "[data-testid='internal-track-link'] + div, span[class*='secondary'], a[href*='/artist/']",
        );
        const rowTitle = (titleEl?.textContent || "").trim();
        const rowArtist = (artistEl?.textContent || "").trim();
        const t = rowTitle.toLowerCase();
        const a = rowArtist.toLowerCase();
        const wt = wantTitle.toLowerCase();
        const wa = wantArtist.toLowerCase();
        let score = 0;
        if (t === wt) score += 50;
        else if (t.includes(wt) || wt.includes(t)) score += 30;
        if (wa) {
          if (a === wa) score += 40;
          else if (a.includes(wa) || wa.includes(a)) score += 20;
        }
        if (!best || score > best.score) best = { index, score };
      });
      return best;
    },
    title,
    artist,
  );
}

export async function setSpotifyVolume(page: Page, percent: number): Promise<string> {
  const level = Math.max(0, Math.min(100, percent));
  const slider = await page.$(
    "[data-testid='volume-bar'], input[type='range'][aria-label*='volume' i], input[type='range']",
  );
  if (!slider) {
    return "Volume slider not found — playback may still be active.";
  }
  const box = await slider.boundingBox();
  if (!box) throw new Error("Volume slider not visible.");
  const startX = box.x + box.width * 0.1;
  const endX = box.x + box.width * (level / 100);
  const y = box.y + box.height / 2;
  await humanDrag(page, { x: startX, y }, { x: endX, y });
  return `Spotify volume set to about ${level}%.`;
}

export async function playSpotify(
  page: Page,
  song: string,
  artistHint = "",
): Promise<string> {
  const parsed = parseSongQuery(song);
  const artist = artistHint || parsed.artist;
  const title = parsed.title;
  const query = artist ? `${title} ${artist}` : title;
  const encoded = encodeURIComponent(query);

  await page.goto(`https://open.spotify.com/search/${encoded}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await humanWait(800, 1500);

  const best = await pickBestTrack(page, title, artist);
  const rows = await page.$$("[data-testid='track-row'], [data-testid='tracklist-row']");
  const row = best !== null && rows[best.index] ? rows[best.index] : rows[0];
  if (row) {
    await humanClick(page, row);
    await humanWait(500, 1000);
  }

  const playBtn = await page.$(
    "[data-testid='play-button'], button[aria-label='Play'], button[aria-label*='Play']",
  );
  if (playBtn) {
    await humanClick(page, playBtn);
    await humanWait(400, 800);
    const label = artist ? `${title} by ${artist}` : title;
    return `Playing ${label} on Spotify`;
  }

  const searchInput = await page.$("input[data-testid='search-input'], input[type='search']");
  if (searchInput) {
    await humanType(page, searchInput, query);
    await page.keyboard.press("Enter");
    await humanWait(1000, 1800);
    const first = await page.$("[data-testid='track-row']");
    if (first) await humanClick(page, first);
  }
  return `Opened Spotify search for ${query}`;
}