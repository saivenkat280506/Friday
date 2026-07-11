import type { Browser, Page } from "puppeteer";
import { googleSearch, searchAndBrowse } from "./google.js";
import { playSpotify } from "./spotify.js";
import { playYouTube } from "./youtube.js";
import { mediaControl } from "./media.js";

export type RecipeName =
  | "googleSearch"
  | "searchAndBrowse"
  | "playSpotify"
  | "playYouTube"
  | "playYouTubeMusic"
  | "mediaControl";

export async function runRecipe(
  name: RecipeName,
  page: Page,
  browser: Browser,
  params: Record<string, string>,
): Promise<string> {
  switch (name) {
    case "googleSearch":
      return googleSearch(page, params.query || "");
    case "searchAndBrowse":
      return searchAndBrowse(page, params.query || "");
    case "playSpotify":
      return playSpotify(page, params.song || "");
    case "playYouTube":
      return playYouTube(page, params.song || "", false);
    case "playYouTubeMusic":
      return playYouTube(page, params.song || "", true);
    case "mediaControl":
      return mediaControl(browser, page, params.action || "pause");
    default:
      throw new Error(`Unknown recipe: ${name}`);
  }
}