import Fastify from "fastify";
import cors from "@fastify/cors";
import { executeAction } from "./actions.js";
import { observePage } from "./observer/dom-state.js";
import { runRecipe, type RecipeName } from "./recipes/index.js";
import { sessionManager } from "./session/manager.js";
import type { BrowserMode } from "./types.js";

const PORT = Number(process.env.BROWSER_AGENT_PORT || 9477);

const app = Fastify({ logger: true });
await app.register(cors, { origin: true });

function resolveMode(body: { mode?: string; task?: string }): BrowserMode {
  if (body.mode === "headed" || body.mode === "headless") return body.mode;
  const task = (body.task || "").toLowerCase();
  const complex =
    task.includes("browse") ||
    task.includes("article") ||
    task.includes("research") ||
    task.includes("multiple");
  return complex ? "headless" : "headed";
}

app.get("/health", async () => ({
  ok: true,
  ...sessionManager.status(),
}));

app.post<{ Body: { mode?: BrowserMode } }>("/session/start", async (req) => {
  const mode = resolveMode(req.body || {});
  await sessionManager.start(mode);
  return { success: true, mode };
});

app.post("/session/stop", async () => {
  await sessionManager.stop();
  return { success: true };
});

app.post("/observe", async () => {
  const page = await sessionManager.ensureStarted();
  const browser = sessionManager.getBrowser();
  if (!browser) throw new Error("Browser not available");
  const state = await observePage(page, browser);
  return { success: true, state };
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
      const message = await runRecipe(
        req.params.name as RecipeName,
        page,
        browser,
        req.body || {},
      );
      const state = await observePage(page, browser);
      return { success: true, message, state };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { success: false, message };
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
      hint: "Use backend browser_agent.py for full AI loop",
    };
  },
);

app.listen({ port: PORT, host: "127.0.0.1" }).catch((err) => {
  console.error(err);
  process.exit(1);
});