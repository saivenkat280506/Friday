import type { Browser, Page } from "puppeteer";
import type { PageState } from "../types.js";

const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "textarea",
  "select",
  "[role=button]",
  "[role=link]",
  "[role=textbox]",
  "[role=searchbox]",
  "[contenteditable=true]",
].join(",");

function detectPlatform(url: string): string {
  if (url.includes("open.spotify.com")) return "spotify";
  if (url.includes("music.youtube.com")) return "youtube_music";
  if (url.includes("youtube.com")) return "youtube";
  if (url.includes("google.com")) return "google";
  if (url.includes("chatgpt.com") || url.includes("openai.com")) return "chatgpt";
  if (url.includes("news.google.com")) return "news";
  return "web";
}

export async function observePage(page: Page, browser: Browser): Promise<PageState> {
  const url = page.url();
  const title = await page.title();
  const scrollY = await page.evaluate(() => window.scrollY);
  const pages = await browser.pages();

  const interactive = await page.evaluate((selector) => {
    const nodes = Array.from(document.querySelectorAll(selector));
    const results: Array<{
      role: string;
      text: string;
      selector: string;
      value?: string;
      bbox: [number, number, number, number];
    }> = [];

    const isVisible = (el: Element): boolean => {
      const rect = el.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) return false;
      const style = window.getComputedStyle(el);
      return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
    };

    const cssPath = (el: Element): string => {
      if (el.id) return `#${CSS.escape(el.id)}`;
      const testId = el.getAttribute("data-testid");
      if (testId) return `[data-testid="${testId}"]`;
      const name = el.getAttribute("name");
      if (name) return `${el.tagName.toLowerCase()}[name="${name}"]`;
      const aria = el.getAttribute("aria-label");
      if (aria) return `${el.tagName.toLowerCase()}[aria-label="${aria.replace(/"/g, '\\"')}"]`;
      return el.tagName.toLowerCase();
    };

    for (const el of nodes) {
      if (!isVisible(el)) continue;
      const rect = el.getBoundingClientRect();
      const text =
        (el as HTMLElement).innerText?.trim().slice(0, 80) ||
        el.getAttribute("aria-label") ||
        el.getAttribute("placeholder") ||
        el.getAttribute("title") ||
        "";
      if (!text && el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") continue;
      results.push({
        role: el.getAttribute("role") || el.tagName.toLowerCase(),
        text: text.slice(0, 80),
        selector: cssPath(el),
        value: (el as HTMLInputElement).value || undefined,
        bbox: [
          Math.round(rect.x),
          Math.round(rect.y),
          Math.round(rect.width),
          Math.round(rect.height),
        ],
      });
      if (results.length >= 80) break;
    }
    return results;
  }, INTERACTIVE_SELECTOR);

  const media = await page.evaluate((platform) => {
    const video = document.querySelector("video") as HTMLVideoElement | null;
    const audio = document.querySelector("audio") as HTMLAudioElement | null;
    const mediaEl = video || audio;
    let title = document.title;
    const spotifyTitle = document.querySelector("[data-testid='context-item-info-title']");
    if (spotifyTitle?.textContent) title = spotifyTitle.textContent.trim();
    const ytTitle = document.querySelector("h1.ytd-watch-metadata yt-formatted-string, h1.title");
    if (ytTitle?.textContent) title = ytTitle.textContent.trim();
    return {
      playing: mediaEl ? !mediaEl.paused : false,
      title: title.slice(0, 120),
      platform,
    };
  }, detectPlatform(url));

  return {
    url,
    title,
    interactive,
    media,
    scrollY: Math.round(scrollY),
    tabCount: pages.length,
  };
}