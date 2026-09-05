/**
 * Spotify web automation: login (Continue with Google + email) + search.
 * Credentials: params or env SPOTIFY_EMAIL / SPOTIFY_PASSWORD.
 * Prefer Google SSO when available (matches common user flow).
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { getPage } from "../browser.mjs";
import * as actions from "../actions.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function credsFrom(params = {}) {
  return {
    email: params.email || process.env.SPOTIFY_EMAIL || "",
    password: params.password || process.env.SPOTIFY_PASSWORD || "",
    preferGoogle:
      params.preferGoogle !== false &&
      params.prefer_google !== false &&
      process.env.SPOTIFY_PREFER_GOOGLE !== "0",
  };
}

async function dismissCookies(page) {
  try {
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll("button, [role='button']")];
      const accept = btns.find((b) =>
        /accept|agree|allow all|only necessary|got it/i.test((b.innerText || b.textContent || "").trim())
      );
      if (accept) accept.click();
    });
    await sleep(600);
  } catch {
    /* ignore */
  }
}

/**
 * Optional saved-password helper. On macOS we prefer Chrome autofill / cookies
 * in the cloned profile. The old Windows DPAPI script is skipped if missing.
 */
function readSavedGooglePassword() {
  const script = path.join(__dirname, "get_chrome_password.py");
  if (!fs.existsSync(script)) return "";
  const profile =
    process.env.CHROME_USER_DATA ||
    path.join(__dirname, "..", "chrome-profile-data");
  const email =
    process.env.SPOTIFY_GOOGLE_EMAIL ||
    process.env.CHROME_GOOGLE_EMAIL ||
    "";
  if (!email) {
    console.warn("[spotify] No SPOTIFY_GOOGLE_EMAIL or CHROME_GOOGLE_EMAIL set — skipping saved password lookup.");
    return "";
  }
  const python = process.env.PYTHON || "python3";
  const r = spawnSync(python, [script, profile, "google", email.split("@")[0]], {
    encoding: "utf8",
    timeout: 15000,
  });
  const pwd = (r.stdout || "").trim();
  if (pwd) return pwd;
  const real = path.join(os.homedir(), "Library", "Application Support", "Google", "Chrome");
  const r2 = spawnSync(python, [script, real, "google", email.split("@")[0]], {
    encoding: "utf8",
    timeout: 15000,
  });
  return (r2.stdout || "").trim();
}

/**
 * Trigger Chrome saved-password autofill, else type decrypted saved password.
 */
async function useChromePasswordAutofill(page) {
  const passSel =
    'input[type="password"], input[name="Passwd"], input[name="password"], input[autocomplete="current-password"]';

  try {
    await page.waitForSelector(passSel, { timeout: 12000, visible: true });
  } catch {
    return { filled: false, reason: "no-password-field" };
  }

  const field = await page.$(passSel);
  if (!field) return { filled: false, reason: "no-password-field" };

  // 1) Try Chrome UI autofill (saved password dropdown)
  await field.click({ clickCount: 1 });
  await sleep(500);
  await field.click({ clickCount: 2 });
  await sleep(800);
  await page.keyboard.press("ArrowDown");
  await sleep(350);
  await page.keyboard.press("Enter");
  await sleep(1000);

  let valueLen = await page.$eval(passSel, (el) => (el.value || "").length).catch(() => 0);

  // 2) If autofill empty, decrypt from Chrome Login Data and type it
  if (valueLen < 1) {
    console.log("[spotify] Autofill empty — decrypting saved Google password from Chrome profile…");
    const saved = await readSavedGooglePassword();
    if (saved) {
      await page.click(passSel, { clickCount: 3 });
      await page.keyboard.press("Backspace");
      await page.type(passSel, saved, { delay: 20 });
      valueLen = saved.length;
      await sleep(400);
      await page.keyboard.press("Enter");
      await sleep(1500);
      await page.evaluate(() => {
        const btns = [...document.querySelectorAll("button, div[role='button']")];
        const next = btns.find((b) =>
          /^(next|continue|sign in)$/i.test((b.innerText || "").trim())
        );
        if (next) next.click();
      });
      await sleep(2500);
      return { filled: true, via: "chrome-login-data", length: valueLen };
    }
  }

  if (valueLen > 0) {
    await page.keyboard.press("Enter");
    await sleep(1500);
    await page.evaluate(() => {
      const btns = [...document.querySelectorAll("button, div[role='button']")];
      const next = btns.find((b) =>
        /^(next|continue|sign in)$/i.test((b.innerText || "").trim())
      );
      if (next) next.click();
    });
    await sleep(2000);
    return { filled: true, via: "chrome-autofill", length: valueLen };
  }

  return { filled: false, reason: "no-saved-password-found" };
}

/**
 * Click "Continue with Google" / Google SSO on Spotify login.
 * Returns { clicked, label } or null.
 */
async function clickContinueWithGoogle(page) {
  // Try multiple strategies — Spotify changes markup often
  const clicked = await page.evaluate(() => {
    const labelRe = /continue with google|sign in with google|log in with google|google/i;

    // data-testid / known buttons
    const selectors = [
      'button[data-testid="google-login"]',
      'button[data-encore-id="buttonPrimary"]',
      'button[data-encore-id="buttonSecondary"]',
      'a[data-testid="google-login"]',
      'button[aria-label*="Google" i]',
      'a[aria-label*="Google" i]',
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const t = (el.innerText || el.textContent || el.getAttribute("aria-label") || "").trim();
        if (/google/i.test(t) || sel.includes("google")) {
          el.click();
          return { via: sel, label: t.slice(0, 80) };
        }
      }
    }

    // Any button/link whose visible text mentions Google
    const nodes = [...document.querySelectorAll("button, a, [role='button'], div[role='button']")];
    for (const el of nodes) {
      const t = (el.innerText || el.textContent || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
      if (!t || t.length > 80) continue;
      if (labelRe.test(t) && /google/i.test(t)) {
        el.click();
        return { via: "text", label: t.slice(0, 80) };
      }
    }

    // Sometimes the Google button is an image + span inside a button
    for (const el of nodes) {
      const t = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (t.includes("google") && (t.includes("continue") || t.includes("sign") || t.includes("log"))) {
        el.click();
        return { via: "loose-text", label: t.slice(0, 80) };
      }
    }
    return null;
  });

  return clicked;
}

export async function isLoggedIn() {
  const page = await getPage();
  const url = page.url();
  const state = await page.evaluate(() => {
    const body = document.body?.innerText || "";
    const hasLogin = /log in|sign up/i.test(body) && !/log out|account/i.test(body);
    const avatar = !!document.querySelector(
      '[data-testid="user-widget-link"], [data-testid="user-widget-avatar"]'
    );
    return { hasLogin, avatar, href: location.href };
  });
  return {
    ok: true,
    loggedIn:
      state.avatar ||
      (url.includes("open.spotify.com") && !state.hasLogin && !url.includes("/login")),
    ...state,
  };
}

export async function login(params = {}) {
  const { email, password, preferGoogle } = credsFrom(params);
  const page = await getPage();
  const useGoogle = preferGoogle || params.method === "google" || params.provider === "google";

  await actions.navigate("https://accounts.spotify.com/en/login", {
    waitUntil: "networkidle2",
    timeout: 90000,
  });
  await sleep(1800);
  await dismissCookies(page);

  // Already logged in redirect?
  if (page.url().includes("open.spotify.com") && !page.url().includes("login")) {
    return { ok: true, alreadyLoggedIn: true, url: page.url(), message: "Already logged into Spotify." };
  }

  // ── Prefer Continue with Google (user-requested flow) ───────────────────
  if (useGoogle) {
    // Give OAuth buttons time to hydrate
    await sleep(1000);
    let googleClick = await clickContinueWithGoogle(page);

    // Retry once after a short wait (lazy render)
    if (!googleClick) {
      await sleep(1500);
      googleClick = await clickContinueWithGoogle(page);
    }

    if (googleClick) {
      // Wait for Google accounts / OAuth page or return to Spotify
      await Promise.race([
        page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => null),
        sleep(5000),
      ]);
      await sleep(2000);

      const url = page.url();
      const onGoogle =
        /accounts\.google\.com|google\.com\/o\/oauth|google\.com\/signin/i.test(url);

      // Prefer configured account on chooser / identifier
      const preferred =
        process.env.SPOTIFY_GOOGLE_EMAIL ||
        process.env.CHROME_GOOGLE_EMAIL ||
        "";

      // Account chooser: click matching data-identifier
      if (onGoogle) {
        try {
          let picked = await page.evaluate((email) => {
            const want = (email || "").toLowerCase();
            const nodes = [
              ...document.querySelectorAll("[data-identifier], [data-email], li div[role='link'], div[data-authuser]"),
            ];
            for (const el of nodes) {
              const id = (
                el.getAttribute("data-identifier") ||
                el.getAttribute("data-email") ||
                el.innerText ||
                ""
              ).toLowerCase();
              if (want && id.includes(want.split("@")[0])) {
                el.click();
                return el.innerText?.replace(/\s+/g, " ").trim().slice(0, 100) || want;
              }
            }
            // Fallback: any account tile with @
            for (const el of nodes) {
              if (/@/.test(el.innerText || "")) {
                el.click();
                return (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 100);
              }
            }
            return null;
          }, preferred);

          if (!picked) {
            // Email entry page: type preferred email if input present
            const emailSel = 'input[type="email"], input#identifierId, input[name="identifier"]';
            const hasEmail = await page.$(emailSel);
            if (hasEmail) {
              await page.click(emailSel, { clickCount: 3 });
              await page.type(emailSel, preferred, { delay: 25 });
              await page.keyboard.press("Enter");
              picked = preferred;
              await Promise.race([
                page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => null),
                sleep(4000),
              ]);
            }
          } else {
            await Promise.race([
              page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => null),
              sleep(6000),
            ]);
          }

          // Continue / Next after account pick
          await page.evaluate(() => {
            const btns = [...document.querySelectorAll("button, div[role='button']")];
            const next = btns.find((b) =>
              /^(next|continue|allow|accept)$/i.test((b.innerText || "").trim())
            );
            if (next) next.click();
          });
          await sleep(2000);

          // Password challenge: use Chrome SAVED PASSWORD autofill (real profile)
          if (/challenge\/pwd|password|Passwd/i.test(page.url()) || (await page.$('input[type="password"]'))) {
            console.log("[spotify] Google password page — trying Chrome autofill…");
            const auto = await useChromePasswordAutofill(page);
            console.log("[spotify] autofill result:", auto);
            await Promise.race([
              page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 25000 }).catch(() => null),
              sleep(5000),
            ]);
          }

          // Consent / Allow screen
          await page.evaluate(() => {
            const btns = [...document.querySelectorAll("button, div[role='button']")];
            const allow = btns.find((b) =>
              /^(allow|continue|accept|yes)$/i.test((b.innerText || "").trim())
            );
            if (allow) allow.click();
          });
          await sleep(2500);
        } catch (e) {
          console.warn("[spotify] Google account pick:", e?.message || e);
        }
      }

      await sleep(2500);
      const finalUrl = page.url();
      const stillGoogle = /accounts\.google\.com|google\.com\/o\/oauth|google\.com\/signin/i.test(
        finalUrl
      );
      const onPwd = /challenge\/pwd|password/i.test(finalUrl);

      if (stillGoogle && onPwd) {
        // One more autofill attempt
        const auto = await useChromePasswordAutofill(page);
        await Promise.race([
          page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => null),
          sleep(4000),
        ]);
        const url2 = page.url();
        if (/accounts\.google\.com/i.test(url2) && /challenge\/pwd/i.test(url2)) {
          return {
            ok: true,
            method: "google",
            clicked: googleClick,
            autofill: auto,
            url: url2,
            message:
              "Google password page is open. Chrome autofill did not fill yet — click the password field and pick your saved password, then Next. Session will stick after that.",
          };
        }
      }

      if (stillGoogle && !/open\.spotify\.com/i.test(page.url())) {
        return {
          ok: true,
          method: "google",
          clicked: googleClick,
          url: page.url(),
          message:
            "Continue with Google started. If Google asks for password, use Chrome's saved password (click the field → choose saved login).",
        };
      }

      // Navigate to player if not already
      if (!finalUrl.includes("open.spotify.com")) {
        await actions.navigate("https://open.spotify.com/", {
          waitUntil: "domcontentloaded",
          timeout: 60000,
        });
        await sleep(2000);
      }

      const logged = await isLoggedIn();
      return {
        ok: true,
        method: "google",
        clicked: googleClick,
        loggedIn: logged.loggedIn,
        url: page.url(),
        message: logged.loggedIn
          ? "Spotify login via Google successful."
          : `Clicked "${googleClick.label || "Continue with Google"}". Complete any prompt in the browser if still open.`,
      };
    }

    // Fall through to email if Google button not found and creds exist
    if (!email || !password) {
      return {
        ok: false,
        needsCredentials: true,
        googleButtonMissing: true,
        url: page.url(),
        message:
          "Could not find 'Continue with Google' on Spotify login. Page is open — click it manually, or set SPOTIFY_EMAIL/SPOTIFY_PASSWORD.",
      };
    }
  }

  // ── Email / password fallback ───────────────────────────────────────────
  if (!email || !password) {
    // Still try Google one more time for demos without env creds
    const googleClick = await clickContinueWithGoogle(page);
    if (googleClick) {
      await Promise.race([
        page.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => null),
        sleep(4000),
      ]);
      return {
        ok: true,
        method: "google",
        clicked: googleClick,
        url: page.url(),
        message:
          "Clicked Continue with Google. Finish sign-in in the browser if prompted.",
      };
    }
    return {
      ok: false,
      needsCredentials: true,
      url: page.url(),
      message:
        "Spotify login page is open. Click Continue with Google, or set SPOTIFY_EMAIL and SPOTIFY_PASSWORD.",
    };
  }

  await dismissCookies(page);

  const userSel =
    'input#login-username, input[name="username"], input[data-testid="login-username"], input[type="email"], input[type="text"]';
  const passSel =
    'input#login-password, input[name="password"], input[data-testid="login-password"], input[type="password"]';

  await page.waitForSelector(userSel, { timeout: 20000 });
  await page.click(userSel, { clickCount: 3 });
  await page.type(userSel, email, { delay: 20 });
  await page.click(passSel, { clickCount: 3 });
  await page.type(passSel, password, { delay: 20 });

  const submitted = await page.evaluate(() => {
    const btn =
      document.querySelector('button#login-button, button[data-testid="login-button"], button[type="submit"]') ||
      [...document.querySelectorAll("button")].find((b) => /log in|sign in/i.test(b.innerText || ""));
    if (btn) {
      btn.click();
      return true;
    }
    return false;
  });
  if (!submitted) await page.keyboard.press("Enter");

  await Promise.race([
    page.waitForNavigation({ waitUntil: "networkidle2", timeout: 45000 }).catch(() => null),
    sleep(8000),
  ]);

  await sleep(2000);
  const url = page.url();
  const bodyText = await page.evaluate(() => (document.body?.innerText || "").slice(0, 500));
  const failed = /incorrect|wrong password|couldn't find|try again/i.test(bodyText);

  if (failed) {
    return {
      ok: false,
      url,
      message: "Spotify email login appears to have failed (check credentials or captcha).",
      snippet: bodyText.slice(0, 200),
    };
  }

  if (!url.includes("open.spotify.com")) {
    await actions.navigate("https://open.spotify.com/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await sleep(2000);
  }

  const logged = await isLoggedIn();
  return {
    ok: true,
    method: "email",
    url: page.url(),
    loggedIn: logged.loggedIn,
    message: logged.loggedIn
      ? "Spotify login successful."
      : "Login form submitted. If a captcha or 2FA appeared, complete it once; session will be saved.",
  };
}

export async function search(query, { play = false } = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing Spotify search query");

  const page = await getPage();
  const url = `https://open.spotify.com/search/${encodeURIComponent(q)}`;
  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 90000 });
  await sleep(3000);
  await dismissCookies(page);

  let played = null;
  if (play) {
    played = await page.evaluate(() => {
      const playBtns = [
        ...document.querySelectorAll(
          'button[data-testid="play-button"], button[aria-label*="Play"], [data-testid="top-result-card"] button'
        ),
      ];
      const btn = playBtns[0];
      if (btn) {
        btn.click();
        return { text: (btn.getAttribute("aria-label") || btn.innerText || "play").slice(0, 80) };
      }
      const row = document.querySelector('[data-testid="tracklist-row"], [role="row"]');
      if (row) {
        row.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
        return { text: (row.innerText || "").slice(0, 80) };
      }
      return null;
    });
    await sleep(2000);
  }

  const info = await actions.contentInfo();
  return {
    ok: true,
    query: q,
    played,
    ...info,
    message: play
      ? `Searched Spotify for "${q}"${played ? " and hit play." : " (play button not auto-found — page is open)."}`
      : `Opened Spotify search for "${q}".`,
  };
}
