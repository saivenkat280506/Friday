import { z } from "zod";
import type { Browser } from "puppeteer";
import { humanClick } from "./human/cursor.js";
import { humanScroll } from "./human/scroll.js";
import { humanType } from "./human/typing.js";
import { humanWait } from "./human/wait.js";
import { buildObservation } from "./observer/observation.js";
import { mediaControl } from "./recipes/media.js";
import { isUrlAllowed } from "./safety/allowlist.js";
import { sessionManager } from "./session/manager.js";
import { closeExtraTabs, switchToNewTabIfNeeded } from "./session/tabs.js";
import type { ActionRequest, ActionResult } from "./types.js";

const actionSchema = z.object({
  action: z.string(),
  selector: z.string().optional(),
  text: z.string().optional(),
  url: z.string().optional(),
  key: z.string().optional(),
  direction: z.enum(["up", "down"]).optional(),
  amount: z.number().optional(),
  seconds: z.number().optional(),
  engine: z.string().optional(),
  query: z.string().optional(),
  mediaAction: z.string().optional(),
  allowNewTab: z.boolean().optional(),
  volume: z.number().optional(),
});

export async function executeAction(
  browser: Browser,
  raw: ActionRequest,
  mode?: "headed" | "headless",
): Promise<ActionResult> {
  const parsed = actionSchema.safeParse(raw);
  if (!parsed.success) {
    return { success: false, message: parsed.error.message };
  }
  const req = parsed.data;
  let page = await sessionManager.ensureStarted(mode);
  const lastActions: string[] = [];

  try {
    switch (req.action.toUpperCase()) {
      case "GOTO": {
        if (!req.url || !isUrlAllowed(req.url)) {
          return { success: false, message: "URL not allowed" };
        }
        await page.goto(req.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        await humanWait(500, 1200);
        await closeExtraTabs(browser, page);
        lastActions.push(`navigated to ${req.url}`);
        break;
      }
      case "CLICK": {
        if (!req.selector) return { success: false, message: "selector required" };
        await humanClick(page, req.selector);
        await humanWait(300, 900);
        const active = await switchToNewTabIfNeeded(browser, page, {
          allowSwitch: Boolean(req.allowNewTab),
        });
        if (active !== page) {
          sessionManager.setActivePage(active);
          page = active;
          lastActions.push("switched to linked tab");
        } else {
          lastActions.push(`clicked ${req.selector}`);
        }
        break;
      }
      case "TYPE": {
        if (!req.selector || req.text === undefined) {
          return { success: false, message: "selector and text required" };
        }
        const el = await page.$(req.selector);
        if (!el) return { success: false, message: `Element not found: ${req.selector}` };
        await humanType(page, el, req.text);
        lastActions.push(`typed into ${req.selector}`);
        break;
      }
      case "SCROLL": {
        await humanScroll(page, req.direction || "down", req.amount || 3, true);
        lastActions.push(`scrolled ${req.direction || "down"}`);
        break;
      }
      case "PRESS": {
        if (!req.key) return { success: false, message: "key required" };
        await page.keyboard.press(req.key as Parameters<typeof page.keyboard.press>[0]);
        await humanWait(200, 600);
        lastActions.push(`pressed ${req.key}`);
        break;
      }
      case "WAIT": {
        const seconds = Math.min(req.seconds || 1, 10);
        await humanWait(seconds * 1000, seconds * 1000 + 500);
        lastActions.push(`waited ${seconds}s`);
        break;
      }
      case "SEARCH": {
        const query = req.query || req.text || "";
        if (!query) return { success: false, message: "query required" };
        const engine = (req.engine || "google").toLowerCase();
        const url =
          engine === "news"
            ? `https://news.google.com/search?q=${encodeURIComponent(query)}`
            : engine === "duckduckgo"
              ? `https://duckduckgo.com/?q=${encodeURIComponent(query)}`
              : `https://www.google.com/search?q=${encodeURIComponent(query)}`;
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
        await humanWait(500, 1200);
        await closeExtraTabs(browser, page);
        lastActions.push(`searched ${engine} for ${query}`);
        break;
      }
      case "MEDIA": {
        const action = req.mediaAction || "pause";
        const message = await mediaControl(browser, page, action);
        lastActions.push(`media ${action}`);
        if (req.volume !== undefined && req.volume >= 0) {
          const { setSpotifyVolume } = await import("./recipes/spotify.js");
          const volMsg = await setSpotifyVolume(page, req.volume);
          lastActions.push(volMsg);
        }
        const observation = await buildObservation(page, browser, {
          lastActions,
          voiceMessage: message,
        });
        return {
          success: true,
          message,
          state: observation.state,
          observation,
        };
      }
      case "CLOSETABS": {
        const n = await closeExtraTabs(browser, page, { closeAll: true });
        lastActions.push(`closed ${n} extra tab(s)`);
        break;
      }
      default:
        return { success: false, message: `Unknown action: ${req.action}` };
    }

    const observation = await buildObservation(page, browser, {
      lastActions,
      voiceMessage: `${req.action} completed on ${page.url()}`,
    });
    return {
      success: true,
      message: `${req.action} completed`,
      state: observation.state,
      observation,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const observation = await buildObservation(page, browser, {
      status: "needs_user_input",
      lastActions,
      voiceMessage: `Browser step failed: ${message}`,
    }).catch(() => undefined);
    return { success: false, message, observation };
  }
}