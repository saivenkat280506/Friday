import { z } from "zod";
import type { Browser } from "puppeteer";
import { humanClick } from "./human/cursor.js";
import { humanScroll } from "./human/scroll.js";
import { humanType } from "./human/typing.js";
import { humanWait } from "./human/wait.js";
import { observePage } from "./observer/dom-state.js";
import { isUrlAllowed } from "./safety/allowlist.js";
import { sessionManager } from "./session/manager.js";
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
  const page = await sessionManager.ensureStarted(mode);

  try {
    switch (req.action.toUpperCase()) {
      case "GOTO": {
        if (!req.url || !isUrlAllowed(req.url)) {
          return { success: false, message: "URL not allowed" };
        }
        await page.goto(req.url, { waitUntil: "domcontentloaded", timeout: 30000 });
        await humanWait();
        break;
      }
      case "CLICK": {
        if (!req.selector) return { success: false, message: "selector required" };
        await humanClick(page, req.selector);
        await humanWait(300, 800);
        break;
      }
      case "TYPE": {
        if (!req.selector || req.text === undefined) {
          return { success: false, message: "selector and text required" };
        }
        const el = await page.$(req.selector);
        if (!el) return { success: false, message: `Element not found: ${req.selector}` };
        await humanType(page, el, req.text);
        break;
      }
      case "SCROLL": {
        await humanScroll(page, req.direction || "down", req.amount || 3);
        break;
      }
      case "PRESS": {
        if (!req.key) return { success: false, message: "key required" };
        await page.keyboard.press(req.key as Parameters<typeof page.keyboard.press>[0]);
        await humanWait(200, 500);
        break;
      }
      case "WAIT": {
        const seconds = Math.min(req.seconds || 1, 10);
        await humanWait(seconds * 1000, seconds * 1000 + 400);
        break;
      }
      case "SEARCH": {
        const query = req.query || req.text || "";
        if (!query) return { success: false, message: "query required" };
        const engine = (req.engine || "google").toLowerCase();
        const url =
          engine === "duckduckgo"
            ? `https://duckduckgo.com/?q=${encodeURIComponent(query)}`
            : `https://www.google.com/search?q=${encodeURIComponent(query)}`;
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
        await humanWait();
        break;
      }
      default:
        return { success: false, message: `Unknown action: ${req.action}` };
    }

    const state = await observePage(page, browser);
    return { success: true, message: `${req.action} completed`, state };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { success: false, message };
  }
}