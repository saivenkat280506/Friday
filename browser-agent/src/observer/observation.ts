import type { Browser, Page } from "puppeteer";
import type { BrowserObservation, PageState } from "../types.js";
import { observePage } from "./dom-state.js";
import { captureScreenshot } from "./screenshot.js";

export async function buildObservation(
  page: Page,
  browser: Browser,
  opts: {
    status?: BrowserObservation["status"];
    lastActions?: string[];
    extractedData?: Record<string, unknown>;
    voiceMessage?: string;
    nextOptions?: string[];
    captureShot?: boolean;
    shotLabel?: string;
  } = {},
): Promise<BrowserObservation> {
  const state = await observePage(page, browser);
  let screenshot_path: string | undefined;
  let screenshot_base64: string | undefined;

  let screenshot_error: string | undefined;
  if (opts.captureShot !== false) {
    try {
      const shot = await captureScreenshot(page, opts.shotLabel || "observe");
      screenshot_path = shot.path;
      screenshot_base64 = shot.base64;
    } catch (err) {
      screenshot_error = err instanceof Error ? err.message : String(err);
    }
  }

  const voice =
    opts.voiceMessage ||
    `On ${state.title || state.url}. ${state.media.playing ? `Playing ${state.media.title}.` : "Ready for your next step."}`;

  return {
    status: opts.status || "success",
    current_url: state.url,
    last_actions: opts.lastActions || [],
    extracted_data: {
      title: state.title,
      media: state.media,
      scroll_y: state.scrollY,
      tab_count: state.tabCount,
      ...(screenshot_error ? { screenshot_error } : {}),
      ...(opts.extractedData || {}),
    },
    screenshot_path,
    screenshot_base64,
    voice_message: voice,
    next_options: opts.nextOptions || defaultNextOptions(state),
    state,
  };
}

function defaultNextOptions(state: PageState): string[] {
  const options: string[] = [];
  if (state.media.platform === "spotify") {
    options.push("Pause playback?", "Skip to next track?", "Lower volume?");
  } else if (state.url.includes("chatgpt.com") || state.url.includes("openai.com")) {
    options.push("Send another prompt?", "Copy the latest response?");
  } else if (state.url.includes("news.google.com") || state.url.includes("google.com")) {
    options.push("Open an article?", "Scroll for more results?", "Summarize findings?");
  } else {
    options.push("Continue on this page?", "Search for something else?");
  }
  return options.slice(0, 4);
}