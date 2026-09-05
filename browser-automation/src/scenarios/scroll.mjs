/**
 * Scroll performance / demo scenario.
 * Default is demo-friendly: smooth + slower so it is visible on screen recordings.
 */
import * as actions from "../actions.mjs";

export async function scrollSpeedTest({
  url = "https://en.wikipedia.org/wiki/AC/DC",
  pixels = 350,
  times = 4,
  delayMs = 5000,
  behavior = "smooth",
  settleMs = 800,
  /** speed mode: "demo" (slow/visible) or "benchmark" (fast/instant) */
  mode = "demo",
} = {}) {
  const isBench = String(mode).toLowerCase() === "benchmark" || String(mode).toLowerCase() === "fast";
  const cfg = isBench
    ? { pixels: pixels || 900, times: times || 8, delayMs: delayMs || 100, behavior: "instant", settleMs: 0 }
    : {
        // Visible camera-friendly scroll: exactly 5s gap between steps
        pixels: Math.min(Number(pixels) || 350, 420),
        times: Number(times) || 4,
        delayMs: 5000, // hard-coded 5 second gap (user requirement)
        behavior: "smooth",
        settleMs: Math.min(Number(settleMs) || 800, 1000),
      };

  await actions.navigate(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await actions.wait(1500);

  const t0 = performance.now();
  const result = await actions.scroll({
    pixels: cfg.pixels,
    direction: "down",
    times: cfg.times,
    delayMs: cfg.delayMs,
    behavior: cfg.behavior,
    settleMs: cfg.settleMs,
  });
  const wallMs = performance.now() - t0;

  return {
    ok: true,
    url,
    mode: isBench ? "benchmark" : "demo",
    ...result,
    wallClockMs: Number(wallMs.toFixed(2)),
    scrollsPerSecond: Number(((cfg.times / wallMs) * 1000).toFixed(2)),
    message: isBench
      ? `Scroll benchmark: ${cfg.times}×${cfg.pixels}px, avg ${result.avgScrollMs} ms/scroll, wall ${wallMs.toFixed(0)} ms.`
      : `Scroll demo: ${cfg.times} smooth steps of ${cfg.pixels}px (~${(wallMs / 1000).toFixed(1)}s total) — easy to see on camera.`,
  };
}
