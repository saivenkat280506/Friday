import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import puppeteer from "puppeteer";
import type { Browser, Page } from "puppeteer";
import type { BrowserMode } from "../types.js";

const DEFAULT_PORT = Number(process.env.BROWSER_AGENT_PORT || 9477);

function resolveChromePath(): string | undefined {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const candidates = [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    path.join(
      process.env.LOCALAPPDATA || "",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe",
    ),
  ];
  return candidates.find((p) => p && fs.existsSync(p));
}

function resolveUserDataDir(): string {
  if (process.env.CHROME_USER_DATA_DIR) {
    return process.env.CHROME_USER_DATA_DIR;
  }
  return path.join(
    process.env.LOCALAPPDATA || os.homedir(),
    "Google",
    "Chrome",
    "User Data",
  );
}

function puppeteerProfileDir(): string {
  const base = path.join(
    process.env.FRIDAY_BROWSER_PROFILE_DIR ||
      path.join(os.homedir(), ".friday", "browser-profile"),
  );
  fs.mkdirSync(base, { recursive: true });
  return base;
}

export class SessionManager {
  private browser: Browser | null = null;
  private page: Page | null = null;
  private mode: BrowserMode = "headed";

  get activePage(): Page {
    if (!this.page) throw new Error("Browser session not started");
    return this.page;
  }

  get isActive(): boolean {
    return this.browser !== null && this.page !== null;
  }

  getBrowser(): Browser | null {
    return this.browser;
  }

  async start(mode: BrowserMode = "headed"): Promise<void> {
    if (this.browser) return;
    this.mode = mode;
    const chromePath = resolveChromePath();
    const profile = process.env.CHROME_PROFILE || "Default";
    const cdpUrl = process.env.CHROME_CDP_URL || "http://127.0.0.1:9222";

    try {
      this.browser = await puppeteer.connect({
        browserURL: cdpUrl,
        defaultViewport: { width: 1366, height: 768 },
      });
    } catch {
      const userDataDir = puppeteerProfileDir();
      const sourceDir = resolveUserDataDir();
      if (process.env.CHROME_SYNC_PROFILE === "1" && fs.existsSync(sourceDir)) {
        // Lightweight sync: copy cookies file if present (best-effort).
        const srcCookies = path.join(sourceDir, profile, "Cookies");
        const dstCookies = path.join(userDataDir, profile, "Cookies");
        fs.mkdirSync(path.dirname(dstCookies), { recursive: true });
        if (fs.existsSync(srcCookies)) {
          try {
            fs.copyFileSync(srcCookies, dstCookies);
          } catch {
            /* profile may be locked while Chrome is open */
          }
        }
      }

      this.browser = await puppeteer.launch({
        headless: mode === "headless",
        executablePath: chromePath,
        userDataDir,
        args: [
          `--profile-directory=${profile}`,
          "--no-first-run",
          "--no-default-browser-check",
          "--disable-blink-features=AutomationControlled",
          "--window-size=1366,768",
        ],
        defaultViewport: { width: 1366, height: 768 },
      });
    }

    const browser = this.browser;
    if (!browser) throw new Error("Browser failed to start");
    const pages = await browser.pages();
    this.page = pages[0] || (await browser.newPage());
    await this.page.bringToFront();
  }

  async stop(): Promise<void> {
    if (this.browser) {
      await this.browser.close().catch(() => undefined);
    }
    this.browser = null;
    this.page = null;
  }

  async ensureStarted(mode?: BrowserMode): Promise<Page> {
    if (!this.isActive) {
      await this.start(mode || this.mode);
    }
    return this.activePage;
  }

  status() {
    return {
      active: this.isActive,
      mode: this.mode,
      port: DEFAULT_PORT,
    };
  }
}

export const sessionManager = new SessionManager();