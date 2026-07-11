import type { Page } from "puppeteer";
import { humanClick } from "../human/cursor.js";
import { humanType } from "../human/typing.js";
import { humanWait } from "../human/wait.js";
import { humanScroll } from "../human/scroll.js";

export async function googleSearch(page: Page, query: string): Promise<string> {
  await page.goto("https://www.google.com", { waitUntil: "domcontentloaded", timeout: 30000 });
  await humanWait();

  const consent = await page.$("button#L2AGLb, button[aria-label='Accept all']");
  if (consent) {
    await humanClick(page, consent);
    await humanWait(400, 900);
  }

  const searchBox = await page.$("textarea[name='q'], input[name='q']");
  if (!searchBox) throw new Error("Google search box not found");
  await humanType(page, searchBox, query);
  await humanWait(300, 700);
  await page.keyboard.press("Enter");
  await page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => undefined);
  return `Searched Google for: ${query}`;
}

export async function searchAndBrowse(page: Page, query: string): Promise<string> {
  await googleSearch(page, query);
  await humanWait(500, 1000);

  const firstResult = await page.$("div#search a h3");
  if (!firstResult) throw new Error("No Google search results found");
  const linkHandle = await page.evaluateHandle((el) => el.closest("a"), firstResult);
  const linkEl = linkHandle.asElement();
  if (!linkEl) throw new Error("Could not resolve first result link");
  await humanClick(page, linkEl as import("puppeteer").ElementHandle<Element>);
  await page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => undefined);
  await humanWait(600, 1200);
  await humanScroll(page, "down", 4);
  return `Opened first result for: ${query}`;
}