/**
 * YouTube playback + aggressive Skip Ad.
 *
 * Skip strategies (in order):
 *  1) Shadow-DOM walk + real mouse click at button center
 *  2) Classic CSS selectors + Puppeteer handle.click
 *  3) Text locator "Skip Ad" / "Skip Ads"
 *  4) Position click near bottom-right of player (where Skip lives)
 *  5) If ad still stuck → new tab + close old (no double audio)
 */
import { getPage, replaceWithNewTab } from "../browser.mjs";
import * as actions from "../actions.mjs";

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function safeEvaluate(page, fn, ...args) {
  try {
    if (!page || page.isClosed()) return null;
    return await page.evaluate(fn, ...args);
  } catch (err) {
    const msg = String(err?.message || err);
    if (/detached Frame|Execution context was destroyed|Target closed|Session closed/i.test(msg)) {
      return null;
    }
    throw err;
  }
}

async function isAdPlaying(page) {
  const res = await safeEvaluate(page, () => {
    const player = document.querySelector(".html5-video-player, #movie_player");
    if (!player) return false;
    const cls = player.className || "";
    if (/\bad-showing\b|\bad-interrupting\b|\bad-created\b/i.test(cls)) return true;
    if (
      document.querySelector(
        ".ytp-ad-player-overlay, .ytp-ad-text, .ytp-ad-preview-text, .ytp-ad-duration-remaining, .ytp-ad-simple-ad-badge, .ytp-ad-visit-advertiser-button, .video-ads.ytp-ad-module"
      )
    ) {
      return true;
    }
    // Skip button present usually means ad
    if (document.querySelector(".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern")) {
      return true;
    }
    return false;
  });
  return !!res;
}

async function mouseClick(page, x, y) {
  try {
    await page.mouse.move(x, y, { steps: 6 });
    await sleep(60);
    await page.mouse.down();
    await sleep(40);
    await page.mouse.up();
    return true;
  } catch {
    return false;
  }
}

/**
 * Single skip attempt.
 */
async function tryClickSkipOnce(page) {
  if (!page || page.isClosed()) return null;

  // 1) Shadow walk → coordinates
  try {
    const target = await safeEvaluate(page, () => {
      function* walk(root) {
        const nodes = root.querySelectorAll("*");
        for (const n of nodes) {
          yield n;
          if (n.shadowRoot) yield* walk(n.shadowRoot);
        }
      }
      for (const el of walk(document)) {
        const cls = String(el.className?.baseVal || el.className || "");
        const label = (
          el.innerText ||
          el.textContent ||
          el.getAttribute?.("aria-label") ||
          ""
        )
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, 80);
        if (/navigation/i.test(label)) continue;

        const classSkip = /ytp-skip-ad|ytp-ad-skip|videoAdUiSkip|skip-ad/i.test(cls);
        const textSkip = /skip\s*ads?/i.test(label);
        const bareSkip = /^skip$/i.test(label) && classSkip;
        if (!classSkip && !textSkip && !bareSkip) continue;

        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const disabled =
          el.disabled ||
          el.getAttribute?.("aria-disabled") === "true" ||
          /disabled/i.test(cls);
        if (disabled) continue;
        if (r.width < 4 || r.height < 4) continue;
        if (style.display === "none" || style.visibility === "hidden") continue;
        if (parseFloat(style.opacity || "1") < 0.05) continue;

        return {
          label: label || "Skip Ad",
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
          cls: cls.slice(0, 100),
        };
      }
      return null;
    });

    if (target?.x != null) {
      const ok = await mouseClick(page, target.x, target.y);
      if (ok) return { via: "shadow-mouse", label: target.label, cls: target.cls };
    }
  } catch {
    /* continue */
  }

  // 2) CSS selectors on all frames
  const SELECTORS = [
    "button.ytp-skip-ad-button",
    ".ytp-skip-ad-button",
    "button.ytp-ad-skip-button-modern",
    ".ytp-ad-skip-button-modern",
    "button.ytp-ad-skip-button",
    ".ytp-ad-skip-button",
    ".ytp-ad-skip-button-container button",
    ".ytp-ad-skip-button-slot button",
    ".ytp-skip-ad-button__text",
    "button.videoAdUiSkipButton",
    ".videoAdUiSkipButton",
  ];

  for (const frame of page.frames()) {
    for (const sel of SELECTORS) {
      try {
        const el = await frame.$(sel);
        if (!el) continue;
        const box = await el.boundingBox();
        if (!box || box.width < 4) {
          await el.dispose().catch(() => {});
          continue;
        }
        try {
          await el.click({ delay: 50 });
        } catch {
          await mouseClick(page, box.x + box.width / 2, box.y + box.height / 2);
        }
        await el.dispose().catch(() => {});
        return { via: sel, label: "Skip Ad" };
      } catch {
        /* next */
      }
    }
  }

  // 3) Text-based element search (using $$ for ElementHandles, not Locator API)
  for (const text of ["Skip Ads", "Skip Ad", "Skip"]) {
    try {
      const handles = await page.$$(`xpath/.//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '${text.toLowerCase()}')]`);
      for (const el of handles) {
        const box = await el.boundingBox().catch(() => null);
        if (!box || box.width < 4 || box.height < 4) {
          await el.dispose().catch(() => {});
          continue;
        }
        // Skip navigation buttons that happen to contain "Skip"
        if (text === "Skip" && box.y < 100) {
          await el.dispose().catch(() => {});
          continue;
        }
        try {
          await el.click({ delay: 50 });
        } catch {
          await mouseClick(page, box.x + box.width / 2, box.y + box.height / 2);
        }
        await el.dispose().catch(() => {});
        return { via: "text-search", label: text };
      }
    } catch {
      /* next */
    }
  }

  // 4) Direct DOM click injection — real Skip elements only (no blind corner clicks)
  const injected = await safeEvaluate(page, () => {
    const sels = [
      "button.ytp-skip-ad-button",
      "button.ytp-ad-skip-button-modern",
      "button.ytp-ad-skip-button",
      ".ytp-ad-skip-button-container button",
      ".ytp-skip-ad-button",
      ".ytp-ad-skip-button-slot button",
    ];
    for (const s of sels) {
      const b = document.querySelector(s);
      if (!b) continue;
      const r = b.getBoundingClientRect();
      const dis =
        b.disabled ||
        b.getAttribute("aria-disabled") === "true" ||
        (b.className && String(b.className).includes("disabled"));
      if (dis || r.width < 4) continue;
      b.focus();
      b.click();
      b.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
      b.dispatchEvent(new PointerEvent("pointerup", { bubbles: true }));
      b.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
      return { sel: s, label: (b.innerText || b.getAttribute("aria-label") || "Skip Ad").trim() };
    }
    const player = document.querySelector(".html5-video-player, #movie_player");
    if (player) {
      const btns = player.querySelectorAll("button");
      for (const b of btns) {
        const t = (b.innerText || b.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
        if (!/skip\s*ads?/i.test(t) || /navigation/i.test(t)) continue;
        b.click();
        return { sel: "player-text", label: t };
      }
    }
    return null;
  });
  if (injected) return { via: "inject", label: injected.label || injected.sel || "Skip Ad" };

  return null;
}

export async function skipAds(page, { maxWaitMs = 35000, pollMs = 400 } = {}) {
  const skips = [];
  const deadline = Date.now() + maxWaitMs;
  let lastSkipAt = 0;

  // Explicit wait for a real enabled Skip button (YouTube unlocks it after ~5s)
  try {
    await page.waitForFunction(
      () => {
        const sels = [
          "button.ytp-skip-ad-button",
          "button.ytp-ad-skip-button-modern",
          "button.ytp-ad-skip-button",
          ".ytp-ad-skip-button-container button",
          ".ytp-skip-ad-button",
        ];
        for (const s of sels) {
          const b = document.querySelector(s);
          if (!b) continue;
          const r = b.getBoundingClientRect();
          const dis =
            b.disabled ||
            b.getAttribute("aria-disabled") === "true" ||
            String(b.className || "").includes("disabled");
          if (!dis && r.width > 4 && r.height > 4) return true;
        }
        // text in player
        const player = document.querySelector(".html5-video-player, #movie_player");
        if (player) {
          for (const b of player.querySelectorAll("button")) {
            const t = (b.innerText || b.getAttribute("aria-label") || "").trim();
            if (/skip\s*ads?/i.test(t) && !/navigation/i.test(t)) {
              const r = b.getBoundingClientRect();
              if (r.width > 4) return true;
            }
          }
        }
        return false;
      },
      { timeout: Math.min(maxWaitMs, 28000), polling: 300 }
    );
  } catch {
    /* no skip button appeared within wait — continue polling / fallback */
  }

  while (Date.now() < deadline) {
    if (!page || page.isClosed()) break;

    if (Date.now() - lastSkipAt < 900) {
      await sleep(pollMs);
      continue;
    }

    const hit = await tryClickSkipOnce(page);
    if (hit) {
      skips.push({ ...hit, t: Date.now() });
      lastSkipAt = Date.now();
      console.log("[youtube] skip click:", hit);
      await sleep(1200);
      if (!(await isAdPlaying(page))) break;
      continue;
    }

    const ad = await isAdPlaying(page);
    if (!ad && skips.length > 0) break;
    if (!ad && skips.length === 0 && Date.now() > deadline - 5000) break;
    await sleep(pollMs);
  }

  return skips;
}

async function muteAndPause(page) {
  await safeEvaluate(page, () => {
    document.querySelectorAll("video, audio").forEach((m) => {
      try {
        m.muted = true;
        m.volume = 0;
        m.pause();
      } catch {
        /* ignore */
      }
    });
  });
}

async function forcePlay(page) {
  if (!page || page.isClosed()) return;

  // Real pointer click on large play / YT Music play-pause when paused
  try {
    const box = await safeEvaluate(page, () => {
      const candidates = [
        ...document.querySelectorAll(
          [
            "#play-pause-button[title='Play']",
            "button[aria-label='Play']",
            "tp-yt-paper-icon-button#play-pause-button[title='Play']",
            ".ytp-large-play-button",
            "button.ytp-play-button[data-title-no-tooltip='Play']",
            "ytmusic-play-button-renderer",
            "button[aria-label*='Play']",
          ].join(",")
        ),
      ];
      for (const el of candidates) {
        const label = (
          el.getAttribute("title") ||
          el.getAttribute("aria-label") ||
          el.getAttribute("data-title-no-tooltip") ||
          ""
        ).toLowerCase();
        // Skip explicit Pause buttons
        if (label.includes("pause")) continue;
        if (label && !label.includes("play") && !el.className?.toString?.().includes("play")) {
          continue;
        }
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }
      const v = document.querySelector("video");
      if (v) {
        const r = v.getBoundingClientRect();
        if (r.width > 4) return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      }
      return null;
    });
    if (box) await mouseClick(page, box.x, box.y);
  } catch {
    /* ignore */
  }

  await safeEvaluate(page, async () => {
    const v = document.querySelector("video, audio");
    if (!v) return;
    try {
      v.muted = false;
      v.volume = 1;
      if (v.paused) await v.play();
    } catch {
      try {
        // Autoplay policy: mute then play then unmute
        v.muted = true;
        if (v.paused) await v.play();
        await new Promise((r) => setTimeout(r, 200));
        v.muted = false;
        v.volume = 1;
      } catch {
        /* ignore */
      }
    }
  });
  await sleep(400);
  // NEVER press "k" blindly — it toggles pause and stops music that already started.
}

async function handleAdsWithFallback(page, watchUrl) {
  let live = page;
  let adSkips = [];
  let usedNewTab = false;

  await forcePlay(live);
  await sleep(1500);

  // Always poll skip for a long window — ads often start after 1–3s
  adSkips = await skipAds(live, { maxWaitMs: 20000, pollMs: 350 });

  if (await isAdPlaying(live)) {
    const more = await skipAds(live, { maxWaitMs: 15000, pollMs: 350 });
    adSkips = adSkips.concat(more);
  }

  // Still ad and no successful content skip → new tab
  if (await isAdPlaying(live)) {
    console.log("[youtube] Ad stuck without skip — new tab");
    await muteAndPause(live);
    live = await replaceWithNewTab(watchUrl, live);
    usedNewTab = true;
    await sleep(2500);
    await forcePlay(live);
    adSkips = adSkips.concat(
      (await skipAds(live, { maxWaitMs: 20000, pollMs: 350 })).map((s) => ({
        ...s,
        onNewTab: true,
      }))
    );
  }

  await forcePlay(live);
  await sleep(1000);
  adSkips = adSkips.concat(await skipAds(live, { maxWaitMs: 8000, pollMs: 350 }));
  await forcePlay(live);

  let playing = null;
  for (let i = 0; i < 6; i++) {
    playing = await safeEvaluate(live, () => {
      const v = document.querySelector("video");
      const player = document.querySelector(".html5-video-player, #movie_player");
      if (!v) return null;
      return {
        paused: v.paused,
        currentTime: v.currentTime,
        muted: v.muted,
        adShowing: !!(
          player &&
          (/\bad-showing\b|\bad-interrupting\b/.test(player.className || ""))
        ),
      };
    });
    if (playing && !playing.adShowing && !playing.paused) break;
    if (playing?.adShowing) {
      adSkips = adSkips.concat(await skipAds(live, { maxWaitMs: 4000, pollMs: 300 }));
    }
    await forcePlay(live);
    await sleep(700);
  }

  return { page: live, adSkips, usedNewTab, playing };
}

async function ensureMusicPlaying(page, { tries = 8 } = {}) {
  let last = null;
  for (let i = 0; i < tries; i++) {
    await forcePlay(page);
    await sleep(700);
    last = await safeEvaluate(page, () => {
      const v = document.querySelector("video, audio");
      if (!v) {
        return {
          hasMedia: false,
          paused: true,
          currentTime: 0,
          muted: false,
          readyState: 0,
        };
      }
      return {
        hasMedia: true,
        paused: v.paused,
        currentTime: v.currentTime,
        muted: v.muted,
        readyState: v.readyState,
        duration: v.duration || 0,
      };
    });
    if (last?.hasMedia && !last.paused && last.currentTime >= 0) {
      // Confirm time advances
      await sleep(900);
      const t2 = await safeEvaluate(page, () => {
        const v = document.querySelector("video, audio");
        return v ? { paused: v.paused, currentTime: v.currentTime } : null;
      });
      if (t2 && !t2.paused && t2.currentTime >= (last.currentTime || 0)) {
        return { ...last, ...t2, ok: true };
      }
    }
  }
  return { ...(last || {}), ok: false };
}

export async function playYoutube(query, { service = "youtube" } = {}) {
  const q = (query || "").trim();
  if (!q) throw new Error("Missing song query");

  const { setActivePage } = await import("../browser.mjs");
  let page = await getPage();
  const useMusic = String(service).toLowerCase().includes("music");

  if (useMusic) {
    const searchUrl = `https://music.youtube.com/search?q=${encodeURIComponent(q)}`;
    await actions.navigate(searchUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
    page = await getPage();
    setActivePage(page);
    await page.bringToFront().catch(() => {});
    await sleep(2500);

    // Cookie / consent banners
    await safeEvaluate(page, () => {
      const btns = [...document.querySelectorAll("button, tp-yt-paper-button")];
      const accept = btns.find((b) =>
        /accept all|accept|i agree|reject all|got it|agree/i.test((b.innerText || b.textContent || "").trim())
      );
      if (accept) accept.click();
    });
    await sleep(800);

    // Wait for search results to hydrate
    try {
      await page.waitForSelector(
        "ytmusic-responsive-list-item-renderer, ytmusic-shelf-renderer, a[href*='watch?v=']",
        { timeout: 20000 }
      );
    } catch {
      console.warn("[youtube-music] Results selector timeout — continuing");
    }
    await sleep(1200);
    page = await getPage();

    // Prefer a real watch URL, then navigate (more reliable than synthetic .click())
    let pick = await safeEvaluate(page, () => {
      const rows = [
        ...document.querySelectorAll("ytmusic-responsive-list-item-renderer"),
      ];
      for (const row of rows) {
        const a =
          row.querySelector("a[href*='watch?v=']") ||
          row.querySelector("a.yt-simple-endpoint[href]") ||
          row.querySelector("a[href]");
        if (!a) continue;
        let href = a.href || a.getAttribute("href") || "";
        if (!href) continue;
        if (href.startsWith("/")) href = `https://music.youtube.com${href}`;
        if (!/watch\?v=|watch\//i.test(href) && !/music\.youtube\.com/i.test(href)) continue;
        const title = (
          row.querySelector("yt-formatted-string.title, .title")?.textContent ||
          a.textContent ||
          ""
        )
          .replace(/\s+/g, " ")
          .trim()
          .slice(0, 120);
        // Skip obvious non-song shelves
        if (/podcast|episode|playlist|community/i.test(title) && rows.length > 1) continue;
        return { href, title: title || "", via: "watch-link" };
      }
      // Any watch link on the page
      const any = document.querySelector("a[href*='watch?v=']");
      if (any) {
        let href = any.href;
        if (href.startsWith("/")) href = `https://music.youtube.com${href}`;
        return {
          href,
          title: (any.textContent || "").replace(/\s+/g, " ").trim().slice(0, 120),
          via: "any-watch-link",
        };
      }
      return null;
    });

    if (pick?.href) {
      console.log("[youtube-music] Opening:", pick.href, pick.title);
      await page.goto(pick.href, { waitUntil: "domcontentloaded", timeout: 90000 });
      setActivePage(page);
      await sleep(2500);
    } else {
      // Fallback: mouse-click first visible play control in results
      console.warn("[youtube-music] No watch link — trying play button click");
      const box = await safeEvaluate(page, () => {
        const row = document.querySelector("ytmusic-responsive-list-item-renderer");
        if (!row) return null;
        const btn =
          row.querySelector(
            "button[aria-label*='Play'], ytmusic-play-button-renderer, .ytmusic-play-button-renderer"
          ) || row;
        const r = btn.getBoundingClientRect();
        if (r.width < 4) return null;
        return {
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
          title: (row.innerText || "").split("\n")[0]?.trim()?.slice(0, 120) || "",
        };
      });
      if (box) {
        await mouseClick(page, box.x, box.y);
        pick = { title: box.title, via: "mouse-play", href: page.url() };
      } else {
        throw new Error(
          "Could not find a playable song on YouTube Music search results. " +
            "Sign in may be required in the FRIDAY Chrome profile."
        );
      }
      await sleep(3000);
    }

    page = await getPage();
    setActivePage(page);
    await page.bringToFront().catch(() => {});

    // Light ad handling only (no mute-all / no blind keyboard toggle)
    try {
      await skipAds(page, { maxWaitMs: 8000, pollMs: 400 });
    } catch {
      /* ignore */
    }

    const playing = await ensureMusicPlaying(page, { tries: 10 });
    const info = await actions.contentInfo();
    const title = pick?.title || q;

    if (!playing.ok) {
      console.warn("[youtube-music] Playback did not start — falling back to youtube.com");
      return playYoutube(q, { service: "youtube" });
    }

    return {
      ok: true,
      service: "youtube_music",
      query: q,
      picked: pick,
      adSkips: [],
      usedNewTab: false,
      playing,
      ...info,
      message: `Playing "${title}" on YouTube Music.`,
    };
  }

  const resultsUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`;
  await actions.navigate(resultsUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
  await sleep(1200);
  await actions.dismissBanners();
  await sleep(800);
  page = await getPage();

  const pick = await safeEvaluate(page, () => {
    const renderers = [...document.querySelectorAll("ytd-video-renderer, ytd-rich-item-renderer")];
    for (const r of renderers) {
      const a = r.querySelector("a#video-title, a#video-title-link, a#thumbnail");
      if (!a?.href?.includes("watch?v=")) continue;
      let href = a.href;
      try {
        const id = new URL(href).searchParams.get("v");
        if (id) href = `https://www.youtube.com/watch?v=${id}`;
      } catch {
        /* keep */
      }
      const titleEl = r.querySelector("#video-title, a#video-title-link");
      const title = (
        titleEl?.getAttribute("title") ||
        titleEl?.textContent ||
        a.title ||
        ""
      )
        .replace(/\s+/g, " ")
        .trim();
      return { href, title: title.slice(0, 160) };
    }
    return null;
  });

  if (!pick?.href) throw new Error("No YouTube video results found");

  await page.goto(pick.href, { waitUntil: "domcontentloaded", timeout: 90000 });
  await sleep(2500);
  page = await getPage();

  const handled = await handleAdsWithFallback(page, pick.href);
  page = handled.page || (await getPage());
  const playing = await ensureMusicPlaying(page, { tries: 8 });
  const info = await actions.contentInfo();

  return {
    ok: !!playing.ok,
    service: "youtube",
    query: q,
    picked: pick,
    playing,
    adSkips: handled.adSkips,
    usedNewTab: handled.usedNewTab,
    ...info,
    message: playing.ok
      ? `Playing "${pick.title || q}" on YouTube.${formatSkipMsg(handled)}`
      : `Opened "${pick.title || q}" on YouTube but playback paused. Click play if Chrome blocked autoplay.`,
  };
}

function formatSkipMsg(handled) {
  const n = handled.adSkips?.length || 0;
  const labels = (handled.adSkips || [])
    .map((s) => s.label)
    .filter(Boolean)
    .slice(0, 2)
    .join(", ");
  let msg = "";
  if (n) msg += ` Skipped ad (${labels || "Skip"} ×${n}).`;
  if (handled.usedNewTab) msg += " Opened a new tab and closed the ad tab.";
  return msg;
}
