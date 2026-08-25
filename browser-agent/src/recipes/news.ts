import type { Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanScroll } from "../human/scroll.js";
import { humanWait } from "../human/wait.js";

async function scrapeHeadlines(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const navLabels = new Set(
      [
        "news showcase",
        "entertainment",
        "sports",
        "business",
        "technology",
        "science",
        "health",
        "world",
        "u.s.",
        "local",
      ].map((s) => s.toLowerCase()),
    );
    const items: string[] = [];
    const selectors = [
      "article h3",
      "article h4",
      "a[aria-label][href]",
      "article a[href]",
    ];
    const seen = new Set<string>();

    for (const selector of selectors) {
      const nodes = document.querySelectorAll(selector);
      for (const node of nodes) {
        const text = (node as HTMLElement).innerText?.trim();
        if (!text || text.length < 12) continue;
        const key = text.toLowerCase();
        if (navLabels.has(key)) continue;
        if (seen.has(key)) continue;
        seen.add(key);
        items.push(text.slice(0, 160));
        if (items.length >= 8) return items;
      }
    }
    return items;
  });
}

export async function researchNews(
  page: Page,
  topic: string,
): Promise<{ message: string; headlines: string[] }> {
  const q = encodeURIComponent(topic);
  await page.goto(`https://news.google.com/search?q=${q}&hl=en-US&gl=US&ceid=US:en`, {
    waitUntil: "domcontentloaded",
    timeout: 35000,
  });
  await humanWait(900, 1500);
  await humanScroll(page, "down", 5, true);

  let headlines = await scrapeHeadlines(page);
  if (headlines.length < 2) {
    await humanWait(1200, 2000);
    await humanScroll(page, "down", 3, true);
    headlines = await scrapeHeadlines(page);
  }

  return {
    message: `Found ${headlines.length} headlines for "${topic}" on Google News.`,
    headlines,
  };
}

export async function openNewsArticle(page: Page, index = 0): Promise<string> {
  const links = await page.$$("article a[href]");
  if (!links.length) throw new Error("No news articles visible to open.");
  const pick = Math.min(Math.max(index, 0), links.length - 1);
  await humanClick(page, links[pick]);
  await page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => undefined);
  await humanWait(700, 1400);
  await humanScroll(page, "down", 4, true);
  return `Opened article #${pick + 1} on the same tab.`;
}