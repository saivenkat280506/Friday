import Fastify from "fastify";
import cors from "@fastify/cors";
import { executeAction } from "./actions.js";
import { buildObservation } from "./observer/observation.js";
import { observePage } from "./observer/dom-state.js";
import { runRecipe, type RecipeName } from "./recipes/index.js";
import { sessionManager } from "./session/manager.js";
import type { BrowserMode } from "./types.js";

const PORT = Number(process.env.BROWSER_AGENT_PORT || 9477);

const app = Fastify({ logger: true });
await app.register(cors, { origin: true });

function resolveMode(body: { mode?: string; task?: string }): BrowserMode {
  if (body.mode === "headed" || body.mode === "headless") return body.mode;
  // Default headed so Spotify/ChatGPT stay logged in via persistent profile.
  return "headed";
}

app.get("/health", async () => ({
  ok: true,
  ...sessionManager.status(),
}));

app.post<{ Body: { mode?: BrowserMode } }>("/session/start", async (req) => {
  const mode = resolveMode(req.body || {});
  await sessionManager.start(mode);
  const status = sessionManager.status();
  return { success: true, mode, stealth: status.stealth, connected_via_cdp: status.connected_via_cdp };
});

app.post("/session/stop", async () => {
  await sessionManager.stop();
  return { success: true };
});

app.post("/observe", async () => {
  const page = await sessionManager.ensureStarted();
  const browser = sessionManager.getBrowser();
  if (!browser) throw new Error("Browser not available");
  const observation = await buildObservation(page, browser, {
    voiceMessage: `Observed ${page.url()}`,
  });
  return { success: true, state: observation.state, observation };
});

app.post("/screenshot", async () => {
  const page = await sessionManager.ensureStarted();
  const browser = sessionManager.getBrowser();
  if (!browser) throw new Error("Browser not available");
  const observation = await buildObservation(page, browser, {
    captureShot: true,
    shotLabel: "manual",
    voiceMessage: "Screenshot captured for vision analysis.",
  });
  return {
    success: true,
    screenshot_path: observation.screenshot_path,
    screenshot_base64: observation.screenshot_base64,
    observation,
  };
});

app.post("/action", async (req) => {
  const body = (req.body || {}) as Record<string, unknown>;
  const mode = resolveMode(body as { mode?: string; task?: string });
  await sessionManager.ensureStarted(mode);
  const browser = sessionManager.getBrowser();
  if (!browser) return { success: false, message: "Browser not available" };
  return executeAction(browser, body as never, mode);
});

app.post<{ Params: { name: string }; Body: Record<string, string> }>(
  "/recipe/:name",
  async (req) => {
    const mode = resolveMode(req.body || {});
    const page = await sessionManager.ensureStarted(mode);
    const browser = sessionManager.getBrowser();
    if (!browser) return { success: false, message: "Browser not available" };
    try {
      const recipe = await runRecipe(
        req.params.name as RecipeName,
        page,
        browser,
        req.body || {},
      );
      const observation = await buildObservation(page, browser, {
        lastActions: [`recipe:${req.params.name}`],
        extractedData: recipe.extracted,
        voiceMessage: recipe.message,
      });
      return { success: true, message: recipe.message, state: observation.state, observation };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      const observation = await buildObservation(page, browser, {
        status: "needs_user_input",
        voiceMessage: message,
      }).catch(() => undefined);
      return { success: false, message, observation };
    }
  },
);

app.post<{ Body: { task?: string; mode?: BrowserMode; maxSteps?: number } }>(
  "/task/run",
  async (req) => {
    const task = req.body?.task || "";
    const mode = resolveMode(req.body || {});
    await sessionManager.ensureStarted(mode);
    return {
      success: true,
      message: `Task queued for Python LangChain agent: ${task}`,
      mode,
      hint: "Use backend browser_agent.py for full AI loop with vision + TTS",
    };
  },
);

app.listen({ port: PORT, host: "127.0.0.1" }).catch((err) => {
  console.error(err);
  process.exit(1);
});