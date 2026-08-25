import type { Browser, Page } from "puppeteer";
import { sendChatGptPrompt } from "./chatgpt.js";
import { googleSearch, searchAndBrowse } from "./google.js";
import { mediaControl } from "./media.js";
import { openNewsArticle, researchNews } from "./news.js";
import { playSpotify, setSpotifyVolume } from "./spotify.js";
import { playYouTube } from "./youtube.js";

export type RecipeName =
  | "googleSearch"
  | "searchAndBrowse"
  | "playSpotify"
  | "setSpotifyVolume"
  | "playYouTube"
  | "playYouTubeMusic"
  | "mediaControl"
  | "sendChatGptPrompt"
  | "researchNews"
  | "openNewsArticle";

export interface RecipeResult {
  message: string;
  extracted?: Record<string, unknown>;
}

export async function runRecipe(
  name: RecipeName,
  page: Page,
  browser: Browser,
  params: Record<string, string>,
): Promise<RecipeResult> {
  switch (name) {
    case "googleSearch":
      return { message: await googleSearch(page, params.query || "") };
    case "searchAndBrowse":
      return { message: await searchAndBrowse(page, params.query || "") };
    case "playSpotify":
      return {
        message: await playSpotify(page, params.song || "", params.artist || ""),
      };
    case "setSpotifyVolume": {
      const pct = Number.parseInt(params.volume || params.percent || "50", 10);
      return { message: await setSpotifyVolume(page, pct) };
    }
    case "playYouTube":
      return { message: await playYouTube(page, params.song || "", false) };
    case "playYouTubeMusic":
      return { message: await playYouTube(page, params.song || "", true) };
    case "mediaControl":
      return { message: await mediaControl(browser, page, params.action || "pause") };
    case "sendChatGptPrompt": {
      const out = await sendChatGptPrompt(page, params.prompt || params.text || "");
      return {
        message: out.message,
        extracted: { chatgpt_response: out.responseText },
      };
    }
    case "researchNews": {
      const out = await researchNews(page, params.topic || params.query || "");
      return {
        message: out.message,
        extracted: { headlines: out.headlines },
      };
    }
    case "openNewsArticle":
      return {
        message: await openNewsArticle(page, Number.parseInt(params.index || "0", 10)),
      };
    default:
      throw new Error(`Unknown recipe: ${name}`);
  }
}