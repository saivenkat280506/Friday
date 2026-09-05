/**
 * FRIDAY Puppeteer control plane — HTTP JSON API.
 * Default: http://127.0.0.1:3920
 */
import http from "node:http";
import { closeBrowser, status as browserStatus } from "./browser.mjs";
import { runAction } from "./actions.mjs";
import { playYoutube } from "./scenarios/youtube.mjs";
import { login as spotifyLogin, search as spotifySearch, isLoggedIn as spotifyIsLoggedIn } from "./scenarios/spotify.mjs";
import { scrollSpeedTest } from "./scenarios/scroll.mjs";
import { searchAndScroll } from "./scenarios/web_search.mjs";
import { sendWhatsApp } from "./scenarios/whatsapp.mjs";

const HOST = process.env.PUPPETEER_HOST || "127.0.0.1";
const PORT = Number(process.env.PUPPETEER_PORT || 3920);

function send(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(data),
    "Access-Control-Allow-Origin": "*",
  });
  res.end(data);
}

async function readJson(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

async function handleCommand(body) {
  const action = body.action || body.cmd || body.op;
  const params = body.params || body;

  switch (action) {
    case "health":
    case "status": {
      // Force browser launch so profile mode is visible
      const { ensureBrowser, status: st } = await import("./browser.mjs");
      await ensureBrowser({ headless: process.env.PUPPETEER_HEADLESS === "1" });
      return { ok: true, service: "friday-puppeteer", ...st() };
    }

    case "youtube_play":
    case "play_youtube":
      return playYoutube(params.query || params.song || params.q, {
        service: params.service || "youtube",
      });

    case "youtube_music_play":
      return playYoutube(params.query || params.song || params.q, {
        service: "youtube_music",
      });

    case "spotify_login":
      return spotifyLogin(params);

    case "spotify_search":
      return spotifySearch(params.query || params.q || params.song, {
        play: !!params.play,
      });

    case "spotify_status":
      return spotifyIsLoggedIn();

    case "scroll_test":
      return scrollSpeedTest({
        url: params.url,
        pixels: params.pixels,
        times: params.times,
        delayMs: params.delayMs ?? params.delay_ms,
        behavior: params.behavior,
        settleMs: params.settleMs ?? params.settle_ms,
        mode: params.mode,
      });

    case "web_search":
    case "search_and_scroll":
    case "google_search":
    case "search_brief":
      return searchAndScroll({
        query: params.query || params.q || params.text,
        bounds: params.bounds || null,
      });

    case "whatsapp_send":
    case "whatsapp_web":
      return sendWhatsApp(params);

    case "set_window_bounds":
    case "window_bounds": {
      const { setWindowBounds } = await import("./browser.mjs");
      return setWindowBounds(params.bounds || params);
    }

    case "close":
      await closeBrowser();
      return { ok: true, message: "Browser closed." };

    default:
      // Low-level primitive
      return runAction(action, params);
  }
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    });
    return res.end();
  }

  try {
    if (req.method === "GET" && (req.url === "/" || req.url === "/health")) {
      return send(res, 200, { ok: true, service: "friday-puppeteer", ...browserStatus() });
    }

    if (req.method === "POST" && (req.url === "/command" || req.url === "/")) {
      const body = await readJson(req);
      const result = await handleCommand(body);
      // needsCredentials is a soft failure (page opened); still HTTP 200 for clients
      let statusCode = 200;
      if (result.ok === false) {
        if (result.needsCredentials) {
          statusCode = 200; // soft failure — page is open for manual action
        } else if (result.error) {
          statusCode = 500; // hard failure with error message
        } else {
          statusCode = 400; // client error without specific error string
        }
      }
      return send(res, statusCode, result);
    }

    send(res, 404, { ok: false, error: "Not found. POST /command { action, ...params }" });
  } catch (err) {
    console.error("[puppeteer]", err);
    send(res, 500, { ok: false, error: String(err?.message || err) });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[friday-puppeteer] listening on http://${HOST}:${PORT}`);
  console.log(`[friday-puppeteer] POST /command  e.g. { "action": "youtube_play", "query": "AC/DC Back in Black" }`);
});

process.on("SIGINT", async () => {
  await closeBrowser();
  process.exit(0);
});
process.on("SIGTERM", async () => {
  await closeBrowser();
  process.exit(0);
});
