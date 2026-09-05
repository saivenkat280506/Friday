const { app, BrowserWindow, dialog, ipcMain, globalShortcut, nativeImage } = require("electron");
const path = require("path");
const { spawn, exec } = require("child_process");
const http = require("http");
const net = require("net");
const fs = require("fs");

function ignoreStreamError(stream) {
  if (stream && typeof stream.on === "function") {
    stream.on("error", (err) => {
      if (err && (err.code === "EPIPE" || err.code === "ERR_STREAM_DESTROYED")) {
        return;
      }
    });
  }
}
ignoreStreamError(process.stdout);
ignoreStreamError(process.stderr);

process.on("uncaughtException", (err) => {
  if (err && (err.code === "EPIPE" || err.message?.includes("write EPIPE"))) {
    return;
  }
  try {
    logToFile(`Uncaught exception: ${err?.stack || err}`, true);
  } catch {
    // Ignore logging failures during fatal crash
  }
});

process.on("unhandledRejection", (reason) => {
  try {
    logToFile(`Unhandled rejection: ${reason?.stack || reason}`, true);
  } catch {
    // Ignore logging failures
  }
});

const PROJECT_ROOT = path.join(__dirname, "../..");
const BACKEND_DIR = path.join(PROJECT_ROOT, "backend");
const FRONTEND_DIR = path.join(PROJECT_ROOT, "frontend");

if (process.platform === "darwin") {
  const currentPath = process.env.PATH || "";
  const extraPaths = ["/opt/homebrew/bin", "/usr/local/bin"];
  const needed = extraPaths.filter((p) => !currentPath.split(":").includes(p));
  if (needed.length > 0) {
    process.env.PATH = `${needed.join(":")}:${currentPath}`;
  }
}

app.setName("F.R.I.D.A.Y.");

function fridayIconPath() {
  const icns = path.join(PROJECT_ROOT, "friday_icon.icns");
  const jpg = path.join(PROJECT_ROOT, "FRIDAY.jpg");
  const jpgLower = path.join(PROJECT_ROOT, "friday.jpg");
  if (fs.existsSync(icns)) return icns;
  if (fs.existsSync(jpg)) return jpg;
  if (fs.existsSync(jpgLower)) return jpgLower;
  return null;
}
const BACKEND_PORT = 8000;
const WEB_PORT = 3000;
const WEB_URL = `http://127.0.0.1:${WEB_PORT}`;
const OLLAMA_PORT = 11434;
const OLLAMA_URL = `http://127.0.0.1:${OLLAMA_PORT}`;

let mainWindow;
let pythonProcess;
let webProcess;
let ollamaProcess;
let ollamaStartedByFriday = false;
let overlayWindow;
let overlayVisible = false;
let overlayReady = false;
let overlayHideTimer;
let hotkeyPollTimer;
let claimHotkeyTimer;
let lastHotkeySeq = 0;
let lastLocalHotkeyAt = 0;
let backendMissCount = 0;
let backendRecoveryInFlight = false;
const HOTKEY_DEBOUNCE_MS = 750;
// Override via COMPANION_HOTKEY / COMPANION_HOTKEY_FALLBACK env vars.
const COMPANION_HOTKEY =
  process.env.COMPANION_HOTKEY ||
  (process.platform === "darwin" ? "Control+Option+Space" : "Alt+Space");
const COMPANION_HOTKEY_FALLBACK =
  process.env.COMPANION_HOTKEY_FALLBACK || "Ctrl+Alt+F";
let registeredCompanionHotkeys = [];
// Alt+Space is the Windows window-menu combo. Electron's globalShortcut may
// report success but still cannot deliver it on Windows.
const electronUnsupportedHotkeys = new Set(
  process.platform === "win32" ? ["Alt+Space", "alt+space"] : []
);

const OVERLAY_WIDTH = 280;
const OVERLAY_WIDTH_WIDE = 420;
const OVERLAY_HEIGHT = 188;
const OVERLAY_HEIGHT_MUSIC = 232;
let overlayWidth = OVERLAY_WIDTH;
const OVERLAY_HTML_PATH = path.join(__dirname, "overlay.html");
const OVERLAY_PRELOAD_PATH = path.join(__dirname, "overlay-preload.js");

function getOverlayPosition() {
  const { screen } = require("electron");
  const workArea = screen.getPrimaryDisplay().workArea;
  return {
    x: Math.round(workArea.x + (workArea.width - overlayWidth) / 2),
    y: workArea.y + 16,
  };
}

function positionOverlayWindow() {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  const { x, y } = getOverlayPosition();
  overlayWindow.setBounds({ x, y, width: overlayWidth, height: OVERLAY_HEIGHT });
}

function sendOverlayVisibility(visible) {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  if (!overlayReady) {
    clearTimeout(sendOverlayVisibility._retryTimer);
    sendOverlayVisibility._retryTimer = setTimeout(
      () => sendOverlayVisibility(visible),
      80
    );
    return;
  }
  overlayWindow.webContents.send("companion-visibility", visible);
}

function postCompanion(path, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: BACKEND_PORT,
        path,
        method: "POST",
        headers: { "Content-Length": "0" },
        timeout: timeoutMs,
      },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      }
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
    req.end();
  });
}

function postCompanionJson(path, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const req = http.request(
      {
        hostname: "127.0.0.1",
        port: BACKEND_PORT,
        path,
        method: "POST",
        headers: { "Content-Length": "0" },
        timeout: timeoutMs,
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => {
          data += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(data || "{}"));
          } catch {
            resolve({ status: res.statusCode === 200 ? "ok" : "error" });
          }
        });
      }
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
    req.end();
  });
}

async function stopVoiceAndTts() {
  await postCompanion("/stop-trigger", 8000);
}

async function activateCompanionMode() {
  await postCompanion("/companion/activate");
}

async function deactivateCompanionMode() {
  await postCompanion("/companion/deactivate");
}

function hideCompanionOverlay() {
  // Companion stays available as the primary surface; only hide on explicit quit.
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  overlayVisible = false;
  sendOverlayVisibility(false);
  clearTimeout(overlayHideTimer);
  overlayHideTimer = setTimeout(() => {
    if (overlayWindow && !overlayWindow.isDestroyed() && !overlayVisible) {
      overlayWindow.hide();
    }
  }, 340);
}

function showCompanionOverlay() {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  overlayVisible = true;
  clearTimeout(overlayHideTimer);
  positionOverlayWindow();
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  overlayWindow.setAlwaysOnTop(true, "screen-saver", 1);
  if (!overlayWindow.isVisible()) {
    overlayWindow.show();
  } else {
    overlayWindow.moveTop();
  }
  sendOverlayVisibility(true);
  activateCompanionMode();
}

function fetchHotkeySignal() {
  return new Promise((resolve) => {
    const req = http.get(
      {
        hostname: "127.0.0.1",
        port: BACKEND_PORT,
        path: "/companion/hotkey-signal",
        timeout: 3000,
      },
      (res) => {
        let data = "";
        res.on("data", (chunk) => {
          data += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve(null);
          }
        });
      }
    );
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.on("error", () => resolve(null));
    req.end();
  });
}

async function dismissCompanionFromHotkey(source = "hotkey") {
  hideCompanionOverlay();
  await stopVoiceAndTts();
  await postCompanion("/agent/stop", 3000);
  await deactivateCompanionMode();
  await postCompanion("/companion/dismiss", 8000);
  logToFile(`Companion closed via ${source} (background work terminated)`);
}

function applyCompanionHotkeyAction(action, source = "hotkey") {
  if (action === "close") {
    // Backend already ran full dismiss for Alt+Space / keyboard hook.
    if (source === "backend-hotkey") {
      hideCompanionOverlay();
      logToFile(`Companion closed via ${source}`);
      return;
    }
    dismissCompanionFromHotkey(source);
    return;
  }
  showCompanionOverlay();
  logToFile(`Companion opened via ${source}`);
}

async function triggerCompanionHotkey(source = "electron") {
  const now = Date.now();
  if (now - lastLocalHotkeyAt < HOTKEY_DEBOUNCE_MS) return;
  lastLocalHotkeyAt = now;
  // Fallback toggle when backend keyboard hook is unavailable.
  const result = await postCompanionJson("/companion/f12");
  if (result && typeof result.seq === "number") {
    lastHotkeySeq = result.seq;
  }
  const action = result && result.action ? result.action : "open";
  applyCompanionHotkeyAction(action, source);
}

async function ensureBackendRunning() {
  if (backendRecoveryInFlight) return false;
  const healthy = await checkBackendHealth();
  if (healthy) {
    backendMissCount = 0;
    return true;
  }

  backendRecoveryInFlight = true;
  try {
    logToFile("Backend offline — restarting for companion hotkey (Alt+Space)");
    await startBackend();
    const ok = await checkBackendHealth();
    if (ok) {
      logToFile("Backend recovered — Alt+Space hook should be active again");
    } else {
      logToFile("Backend recovery failed — try Ctrl+Alt+F or restart FRIDAY", true);
    }
    return ok;
  } catch (err) {
    logToFile(`Backend recovery error: ${err.message}`, true);
    return false;
  } finally {
    backendRecoveryInFlight = false;
  }
}

async function syncCompanionFromBackend() {
  const signal = await fetchHotkeySignal();
  if (!signal || typeof signal.seq !== "number") return;
  lastHotkeySeq = signal.seq;
  if (signal.action === "open" && signal.companion_mode) {
    showCompanionOverlay();
    logToFile("Companion synced open from backend on startup");
  } else if (signal.action === "close") {
    hideCompanionOverlay();
  }
}

function startHotkeyPoll() {
  clearInterval(hotkeyPollTimer);
  let hotkeyPollInFlight = false;
  hotkeyPollTimer = setInterval(async () => {
    if (hotkeyPollInFlight) return;
    hotkeyPollInFlight = true;
    try {
      const signal = await fetchHotkeySignal();
      if (!signal || typeof signal.seq !== "number") {
        backendMissCount += 1;
        if (backendMissCount >= 3) {
          await ensureBackendRunning();
        }
        return;
      }
      backendMissCount = 0;
      if (signal.seq > lastHotkeySeq) {
        lastHotkeySeq = signal.seq;
        applyCompanionHotkeyAction(signal.action, "backend-hotkey");
      }
    } finally {
      hotkeyPollInFlight = false;
    }
  }, 400);
}

function companionHotkeyCombos() {
  const combos = [
    COMPANION_HOTKEY,
    "Control+Option+Space",
    "Control+Alt+Space",
    "Ctrl+Alt+Space",
    "Control+Option+F",
    "Ctrl+Alt+F",
  ];
  if (
    COMPANION_HOTKEY_FALLBACK &&
    !combos.includes(COMPANION_HOTKEY_FALLBACK)
  ) {
    combos.push(COMPANION_HOTKEY_FALLBACK);
  }
  return Array.from(new Set(combos.filter(Boolean)));
}

function isElectronBlockedHotkey(combo) {
  if (process.platform === "win32") {
    return combo.replace(/\s+/g, "").toLowerCase() === "alt+space";
  }
  return false;
}

function registerCompanionHotkey() {
  try {
    globalShortcut.unregister("Alt+Space");
  } catch {
    // Release stale Electron registration so the backend can claim Alt+Space.
  }
  const combos = companionHotkeyCombos();
  for (const combo of registeredCompanionHotkeys) {
    if (!combos.includes(combo)) {
      try {
        globalShortcut.unregister(combo);
      } catch {
        // Ignore unregister failures during rebind
      }
    }
  }

  const active = [];
  for (const combo of combos) {
    if (isElectronBlockedHotkey(combo) || electronUnsupportedHotkeys.has(combo)) {
      logToFile(
        `${combo} handled by Python backend hook (not Electron globalShortcut)`
      );
      continue;
    }
    try {
      globalShortcut.unregister(combo);
      const registered = globalShortcut.register(combo, () => {
        triggerCompanionHotkey(combo);
      });
      if (registered) {
        active.push(combo);
        logToFile(`${combo} mapped to FRIDAY companion (Electron)`);
      } else {
        electronUnsupportedHotkeys.add(combo);
        logToFile(
          `${combo} unavailable in Electron — backend keyboard hook handles it if running`
        );
      }
    } catch (err) {
      electronUnsupportedHotkeys.add(combo);
      logToFile(`${combo} globalShortcut error: ${err}`, true);
    }
  }
  registeredCompanionHotkeys = active;
}

function claimCompanionHotkeys() {
  registerCompanionHotkey();
  if (claimHotkeyTimer) clearInterval(claimHotkeyTimer);
  claimHotkeyTimer = setInterval(() => {
    const combos = companionHotkeyCombos();
    const missing = combos.some(
      (combo) =>
        !electronUnsupportedHotkeys.has(combo) &&
        !globalShortcut.isRegistered(combo)
    );
    if (missing) registerCompanionHotkey();
  }, 5000);
}

function unregisterCompanionHotkey() {
  clearInterval(hotkeyPollTimer);
  if (claimHotkeyTimer) {
    clearInterval(claimHotkeyTimer);
    claimHotkeyTimer = null;
  }
  try {
    for (const combo of registeredCompanionHotkeys) {
      globalShortcut.unregister(combo);
    }
    registeredCompanionHotkeys = [];
    globalShortcut.unregisterAll();
  } catch {
    // Ignore unregister failures during shutdown
  }
}

function readBackendEnv() {
  const envPath = path.join(BACKEND_DIR, ".env");
  const parsed = {};
  try {
    for (const raw of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq < 1) continue;
      parsed[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    }
  } catch {
    // Missing .env is fine — fall back to process.env
  }
  return parsed;
}

function shouldManageOllama() {
  const env = readBackendEnv();
  const provider = (
    process.env.LLM_PROVIDER ||
    env.LLM_PROVIDER ||
    ""
  ).toLowerCase();
  return provider === "ollama";
}

function resolveOllamaBin() {
  const env = readBackendEnv();
  const candidates = [
    process.env.OLLAMA_BIN,
    env.OLLAMA_BIN,
    "/usr/local/bin/ollama",
    "/opt/homebrew/bin/ollama",
    "/usr/bin/ollama",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return "ollama";
}

function runShell(command, timeoutMs = 8000) {
  return new Promise((resolve) => {
    exec(command, { timeout: timeoutMs }, () => resolve());
  });
}

function checkOllamaHealth() {
  return new Promise((resolve) => {
    const req = http.get(`${OLLAMA_URL}/api/tags`, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(2000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function waitForOllama(maxMs = 30000) {
  return new Promise((resolve) => {
    const started = Date.now();
    const poll = setInterval(async () => {
      if (await checkOllamaHealth()) {
        clearInterval(poll);
        resolve(true);
        return;
      }
      if (Date.now() - started >= maxMs) {
        clearInterval(poll);
        resolve(false);
      }
    }, 400);
  });
}

function killListenersOnPort(port) {
  return new Promise((resolve) => {
    if (process.platform === "win32") {
      exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
        if (err || !stdout) {
          resolve(false);
          return;
        }
        const pids = new Set();
        for (const line of stdout.split(/\r?\n/)) {
          if (!line.includes("LISTENING")) continue;
          const parts = line.trim().split(/\s+/);
          const pid = Number.parseInt(parts[parts.length - 1], 10);
          if (Number.isFinite(pid) && pid > 0) pids.add(pid);
        }
        if (!pids.size) {
          resolve(false);
          return;
        }
        exec([...pids].map((pid) => `taskkill /PID ${pid} /F`).join(" & "), () =>
          resolve(true)
        );
      });
      return;
    }
    exec(
      `lsof -nP -iTCP:${port} -sTCP:LISTEN -t`,
      (err, stdout) => {
        const pids = String(stdout || "")
          .trim()
          .split(/\s+/)
          .filter(Boolean);
        if (err || !pids.length) {
          resolve(false);
          return;
        }
        exec(`kill -TERM ${pids.join(" ")}`, () => {
          setTimeout(() => {
            exec(`kill -9 ${pids.join(" ")}`, () => resolve(true));
          }, 400);
        });
      }
    );
  });
}

async function startOllama() {
  if (!shouldManageOllama()) {
    logToFile("LLM_PROVIDER is not ollama — leaving Ollama unmanaged");
    return;
  }
  if (await checkOllamaHealth()) {
    logToFile("Ollama already running on port 11434");
    return;
  }

  const bin = resolveOllamaBin();
  logToFile(`Starting Ollama: ${bin} serve`);
  try {
    ollamaProcess = spawn(bin, ["serve"], {
      cwd: PROJECT_ROOT,
      env: {
        ...process.env,
        OLLAMA_HOST: "127.0.0.1:11434",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    ollamaStartedByFriday = true;
    ollamaProcess.stdout.on("data", (data) => logToFile(`[Ollama] ${data}`));
    ollamaProcess.stderr.on("data", (data) => logToFile(`[Ollama] ${data}`));
    ollamaProcess.on("exit", (code) => {
      logToFile(`Ollama serve exited with code ${code}`);
      ollamaProcess = null;
    });
  } catch (err) {
    logToFile(`Failed to spawn ollama serve: ${err}`, true);
  }

  if (await waitForOllama(20000)) {
    logToFile("Ollama ready on port 11434");
    return;
  }

  if (process.platform === "darwin") {
    logToFile("ollama serve not ready — opening Ollama.app");
    await runShell('open -a Ollama');
    ollamaStartedByFriday = true;
    if (await waitForOllama(20000)) {
      logToFile("Ollama.app is online");
      return;
    }
  }

  throw new Error(
    "Ollama did not start on port 11434.\n\nInstall Ollama, or run: ollama serve"
  );
}

async function stopOllama() {
  if (!shouldManageOllama() && !ollamaProcess && !ollamaStartedByFriday) {
    return;
  }
  const env = readBackendEnv();
  const model = process.env.OLLAMA_MODEL || env.OLLAMA_MODEL || "qwen3.5:4b";
  const bin = resolveOllamaBin();
  logToFile(`Stopping Ollama (model ${model} + server)`);

  await runShell(`"${bin}" stop "${model}"`, 10000);

  if (ollamaProcess && !ollamaProcess.killed) {
    const pid = ollamaProcess.pid;
    ollamaProcess = null;
    if (process.platform === "win32" && pid) {
      await runShell(`taskkill /PID ${pid} /T /F`);
    } else if (pid) {
      try {
        process.kill(pid, "SIGTERM");
      } catch {
        /* ignore */
      }
      await new Promise((r) => setTimeout(r, 400));
      try {
        process.kill(pid, "SIGKILL");
      } catch {
        /* ignore */
      }
    }
  }

  await killListenersOnPort(OLLAMA_PORT);

  if (process.platform === "darwin") {
    await runShell(`osascript -e 'tell application "Ollama" to quit'`);
    await runShell("killall Ollama ollama");
  } else if (process.platform === "win32") {
    await runShell("taskkill /IM ollama.exe /F");
  } else {
    await runShell("pkill -f ollama");
  }

  ollamaStartedByFriday = false;
  logToFile("Ollama stopped");
}

function logToFile(msg, isError = false) {
  try {
    if (isError) {
      console.error(`[Electron Error] ${msg}`);
    } else {
      console.log(`[Electron] ${msg}`);
    }
  } catch {
    // Ignore broken pipe or console write failure
  }
  try {
    const logPath = path.join(
      app.getPath("userData"),
      isError ? "error.log" : "combined.log"
    );
    const timestamp = new Date().toISOString();
    fs.appendFileSync(logPath, `[${timestamp}] ${msg}\n`, "utf-8");
  } catch {
    // Ignore logging failures
  }
}

function killStaleBackendOnPort(port) {
  return killListenersOnPort(port);
}

function isPortInUse(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", (err) => {
      resolve(err.code === "EADDRINUSE");
    });
    server.once("listening", () => {
      server.close();
      resolve(false);
    });
    server.listen(port);
  });
}

function checkBackendHealth() {
  return new Promise((resolve) => {
    const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
      let body = "";
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        if (res.statusCode !== 200) {
          resolve(false);
          return;
        }
        try {
          const data = JSON.parse(body || "{}");
          resolve(data.ready !== false);
        } catch {
          resolve(true);
        }
      });
    });
    req.on("error", () => resolve(false));
    req.setTimeout(5000, () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

function waitForBackend(maxMs = 180000) {
  return new Promise((resolve) => {
    const started = Date.now();
    const poll = setInterval(async () => {
      const healthy = await checkBackendHealth();
      if (healthy) {
        clearInterval(poll);
        resolve(true);
        return;
      }
      if (Date.now() - started >= maxMs) {
        clearInterval(poll);
        resolve(false);
      }
    }, 1000);
  });
}

function checkWebHealth() {
  return new Promise((resolve) => {
    const req = http.get(WEB_URL, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(5000, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function waitForFrontend(maxMs = 120000) {
  return new Promise((resolve) => {
    const started = Date.now();
    const poll = setInterval(async () => {
      const healthy = await checkWebHealth();
      if (healthy) {
        clearInterval(poll);
        resolve(true);
        return;
      }
      if (Date.now() - started >= maxMs) {
        clearInterval(poll);
        resolve(false);
      }
    }, 1000);
  });
}

function killWebProcessTree() {
  return new Promise((resolve) => {
    if (!webProcess || webProcess.killed) {
      resolve(false);
      return;
    }
    const pid = webProcess.pid;
    webProcess = null;
    if (process.platform === "win32") {
      exec(`taskkill /PID ${pid} /T /F`, () => resolve(true));
      return;
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* ignore */
    }
    resolve(true);
  });
}

function startFrontend() {
  return new Promise(async (resolve, reject) => {
    try {
      if (await checkWebHealth()) {
        logToFile(`Frontend already reachable at ${WEB_URL}`);
        resolve();
        return;
      }

      if (await isPortInUse(WEB_PORT)) {
        logToFile(
          `Port ${WEB_PORT} in use by an unreachable or stale process — clearing...`,
          true
        );
        await killListenersOnPort(WEB_PORT);
      }

      const buildIdPath = path.join(FRONTEND_DIR, ".next", "BUILD_ID");
      const useProduction =
        process.env.FRIDAY_WEB_MODE === "production" &&
        fs.existsSync(buildIdPath);
      const script = useProduction ? "start" : "dev:web";

      logToFile(`Starting frontend: npm run ${script}`);
      webProcess = spawn("npm", ["run", script], {
        cwd: FRONTEND_DIR,
        shell: true,
        env: {
          ...process.env,
          PORT: String(WEB_PORT),
        },
      });

      webProcess.stdout.on("data", (data) => {
        logToFile(`[Web] ${data}`);
      });

      webProcess.stderr.on("data", (data) => {
        logToFile(`[Web ERROR] ${data}`, true);
      });

      webProcess.on("exit", (code) => {
        if (code !== 0 && code !== null) {
          logToFile(`Frontend process exited with code ${code}`, true);
        }
      });

      const healthy = await waitForFrontend(120000);
      if (healthy) {
        logToFile(`Frontend ready on port ${WEB_PORT}`);
        resolve();
        return;
      }
      reject(
        new Error(
          `Frontend did not become reachable at ${WEB_URL} within 120 seconds.`
        )
      );
    } catch (err) {
      reject(err);
    }
  });
}

function startBackend() {
  return new Promise(async (resolve, reject) => {
    try {
      const inUse = await isPortInUse(BACKEND_PORT);
      if (inUse) {
        const healthy = await checkBackendHealth();
        if (healthy) {
          logToFile(`Port ${BACKEND_PORT} in use — existing backend is healthy.`);
          resolve();
          return;
        }
        logToFile(
          `Port ${BACKEND_PORT} in use but unhealthy — clearing stale listener`
        );
        try {
          if (pythonProcess && !pythonProcess.killed) {
            pythonProcess.kill("SIGINT");
          }
        } catch {
          // Ignore stale child cleanup failures
        }
        pythonProcess = null;
        await killStaleBackendOnPort(BACKEND_PORT);
        await new Promise((r) => setTimeout(r, 1500));
      }

      const pythonPath = process.platform === "win32"
        ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
        : path.join(BACKEND_DIR, ".venv", "bin", "python");
      const scriptPath = path.join(BACKEND_DIR, "main.py");

      if (!fs.existsSync(pythonPath)) {
        const hint = process.platform === "win32"
          ? "cd backend && python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt"
          : "cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt";
        return reject(
          new Error(
            `Python virtual environment not found.\n\nExpected:\n${pythonPath}\n\nRun: ${hint}`
          )
        );
      }

      if (!fs.existsSync(scriptPath)) {
        return reject(
          new Error(`Backend entry point not found:\n${scriptPath}`)
        );
      }

      logToFile(`Starting backend: ${pythonPath} ${scriptPath}`);

      pythonProcess = spawn(pythonPath, [scriptPath], {
        shell: false,
        cwd: BACKEND_DIR,
        env: {
          ...process.env,
          FOR_DISABLE_CONSOLE_CTRL_HANDLER: "T",
          PYTHONUNBUFFERED: "1",
        },
      });

      pythonProcess.stdout.on("data", (data) => {
        logToFile(`[Backend] ${data}`);
      });

      pythonProcess.stderr.on("data", (data) => {
        logToFile(`[Backend ERROR] ${data}`, true);
      });

      waitForBackend(90000).then((healthy) => {
        if (healthy) {
          logToFile("Backend ready on port 8000");
          resolve();
        } else {
          reject(
            new Error(
              "Backend did not become healthy within 90 seconds.\n\nCheck backend logs or run: npm run stop:desktop"
            )
          );
        }
      });
    } catch (err) {
      reject(err);
    }
  });
}

function createMainWindowInstance() {
  const iconPath = fridayIconPath();

  mainWindow = new BrowserWindow({
    width: 1300,
    height: 850,
    minWidth: 1000,
    minHeight: 650,
    autoHideMenuBar: true,
    title: "F.R.I.D.A.Y.",
    icon: iconPath || undefined,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: false,
      backgroundThrottling: false,
    },
  });

  mainWindow.loadURL(WEB_URL);

  mainWindow.webContents.on("did-fail-load", (_event, errorCode, errorDescription) => {
    logToFile(`Failed to load ${WEB_URL}: ${errorDescription}`, true);
    mainWindow.loadURL(`data:text/html,
      <html>
      <body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;
        font-family:sans-serif;background:#05070a;color:#f1f5f9;">
        <div style="text-align:center;max-width:480px;padding:24px;">
          <h1>F.R.I.D.A.Y.</h1>
          <p style="color:#94a3b8;">Frontend not reachable. Start the dev server first:</p>
          <code style="display:block;margin-top:12px;padding:12px;background:#111827;border-radius:8px;">
            npm run dev:desktop
          </code>
          <p style="font-size:12px;color:#64748b;margin-top:16px;">${errorDescription} (${errorCode})</p>
        </div>
      </body>
      </html>
    `);
  });

  mainWindow.once("ready-to-show", () => {
    if (process.env.FRIDAY_OPEN_MAIN === "1") {
      mainWindow.show();
      mainWindow.focus();
      return;
    }
  });

  mainWindow.on("close", async (event) => {
    if (process.env.FRIDAY_OPEN_MAIN === "1") {
      if (!app.isQuitting) {
        event.preventDefault();
        await shutdownFridayApp();
      }
      return;
    }
    if (!app.isQuitting) {
      event.preventDefault();
      await stopVoiceAndTts();
      mainWindow.hide();
      showCompanionOverlay();
    }
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  return mainWindow;
}

async function createWindow() {
  try {
    await startOllama();
    await startBackend();
    await startFrontend();
  } catch (err) {
    dialog.showErrorBox("FRIDAY Startup Failure", err.message);
    app.quit();
    return;
  }

  createMainWindowInstance();
}

async function openMainApp() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createMainWindowInstance();
  }
  try {
    await startFrontend();
    const currentUrl = mainWindow.webContents.getURL();
    const needsReload =
      !currentUrl.startsWith(WEB_URL) || currentUrl.startsWith("data:");
    if (needsReload) {
      await mainWindow.loadURL(WEB_URL);
      await waitForFrontend(30000);
    }
  } catch (err) {
    logToFile(`openMainApp failed: ${err.message}`, true);
    dialog.showMessageBox(mainWindow, {
      type: "warning",
      title: "FRIDAY Frontend",
      message: "The main app UI is still starting.",
      detail: `${err.message}\n\nThe window will open once http://127.0.0.1:3000 is ready.`,
    });
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function createOverlayWindow() {
  const { screen } = require("electron");
  const { x, y } = getOverlayPosition();
  overlayWindow = new BrowserWindow({
    width: overlayWidth,
    height: OVERLAY_HEIGHT,
    x,
    y,
    transparent: true,
    backgroundColor: "#00000000",
    frame: false,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    thickFrame: false,
    roundedCorners: false,
    show: false,
    webPreferences: {
      preload: OVERLAY_PRELOAD_PATH,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.webContents.on("will-navigate", (event, url) => {
    if (url !== "friday://open-app") return;
    event.preventDefault();
    openMainApp();
  });
  overlayWindow.webContents.on("did-finish-load", () => {
    overlayReady = true;
    if (overlayVisible) sendOverlayVisibility(true);
  });
  overlayWindow.loadFile(OVERLAY_HTML_PATH);

  screen.on("display-metrics-changed", () => {
    if (overlayVisible) positionOverlayWindow();
  });
}

ipcMain.on("overlay-ready", () => {
  overlayReady = true;
  if (overlayVisible) sendOverlayVisibility(true);
});

ipcMain.on("open-main-app", () => {
  openMainApp();
});

function killPythonProcessTree() {
  return new Promise((resolve) => {
    if (!pythonProcess || pythonProcess.killed) {
      resolve(false);
      return;
    }
    const pid = pythonProcess.pid;
    pythonProcess = null;
    if (process.platform === "win32") {
      exec(`taskkill /PID ${pid} /T /F`, () => resolve(true));
      return;
    }
    try {
      process.kill(pid, "SIGTERM");
    } catch {
      /* ignore */
    }
    resolve(true);
  });
}

async function shutdownFridayApp() {
  if (app.isQuitting) return;
  app.isQuitting = true;
  logToFile("Full FRIDAY shutdown — companion voice mode end");

  unregisterCompanionHotkey();
  clearInterval(hotkeyPollTimer);
  if (claimHotkeyTimer) {
    clearInterval(claimHotkeyTimer);
    claimHotkeyTimer = null;
  }

  overlayVisible = false;
  sendOverlayVisibility(false);
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.hide();
  }

  try {
    await stopVoiceAndTts();
    await postCompanion("/agent/stop", 3000);
    await postCompanion("/app/shutdown", 8000);
  } catch (err) {
    logToFile(`Shutdown backend cleanup: ${err}`, true);
  }

  await killPythonProcessTree();
  await killWebProcessTree();
  await killStaleBackendOnPort(BACKEND_PORT);
  await killStaleBackendOnPort(WEB_PORT);
  await stopOllama();

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.destroy();
    mainWindow = null;
  }
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.destroy();
    overlayWindow = null;
  }

  app.exit(0);
}

ipcMain.on("shutdown-friday", () => {
  shutdownFridayApp().catch((err) => {
    logToFile(`Shutdown failed: ${err}`, true);
    app.exit(1);
  });
});

ipcMain.on("dismiss-companion", () => {
  dismissCompanionFromHotkey("overlay-capsule").catch((err) => {
    logToFile(`Dismiss companion error: ${err}`, true);
  });
});

ipcMain.on("overlay-resize", (_event, payload) => {
  if (!overlayWindow || overlayWindow.isDestroyed()) return;
  const next =
    typeof payload === "object" && payload !== null
      ? payload
      : { height: payload };
  const nextHeight = Math.max(
    OVERLAY_HEIGHT,
    Math.min(280, Number(next.height) || OVERLAY_HEIGHT)
  );
  if (next.width) {
    overlayWidth = Math.max(
      OVERLAY_WIDTH,
      Math.min(OVERLAY_WIDTH_WIDE, Number(next.width) || OVERLAY_WIDTH)
    );
  }
  const { x, y } = getOverlayPosition();
  overlayWindow.setBounds({ x, y, width: overlayWidth, height: nextHeight });
});

function cleanupStaleSingletonLock() {
  try {
    const userData = app.getPath("userData");
    const lockPath = path.join(userData, "SingletonLock");
    if (fs.existsSync(lockPath) || fs.lstatSync(lockPath).isSymbolicLink()) {
      const target = fs.readlinkSync(lockPath);
      const match = target.match(/-(\d+)$/);
      if (match) {
        const pid = parseInt(match[1], 10);
        try {
          process.kill(pid, 0);
        } catch (e) {
          if (e.code === "ESRCH") {
            try { fs.unlinkSync(lockPath); } catch {}
            try { fs.unlinkSync(path.join(userData, "SingletonSocket")); } catch {}
            try { fs.unlinkSync(path.join(userData, "SingletonCookie")); } catch {}
          }
        }
      }
    }
  } catch {}
}

cleanupStaleSingletonLock();
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    showCompanionOverlay();
  });
}

app.whenReady().then(async () => {
  const openMainOnStart = process.env.FRIDAY_OPEN_MAIN === "1";
  if (process.platform === "darwin" && app.dock) {
    const iconPath = fridayIconPath();
    if (iconPath) {
      app.dock.setIcon(nativeImage.createFromPath(iconPath));
    }
    app.dock.show();
  }
  createOverlayWindow();
  await createWindow();
  claimCompanionHotkeys();
  if (openMainOnStart) {
    hideCompanionOverlay();
    await deactivateCompanionMode();
    await openMainApp();
    logToFile("FRIDAY main app opened (companion disabled on startup)");
  } else if (process.env.FRIDAY_SHOW_COMPANION === "1") {
    showCompanionOverlay();
    logToFile("FRIDAY companion opened immediately on startup via FRIDAY_SHOW_COMPANION");
  } else {
    await syncCompanionFromBackend();
  }
  startHotkeyPoll();
  const hotkeyHint =
    registeredCompanionHotkeys.length > 0
      ? registeredCompanionHotkeys.join(" or ")
      : COMPANION_HOTKEY;
  logToFile(
    `FRIDAY ready — press ${hotkeyHint} to open companion (backend hook for ${COMPANION_HOTKEY})`
  );
});

app.on("before-quit", async (event) => {
  if (app.isQuitting) return;
  event.preventDefault();
  app.isQuitting = true;
  unregisterCompanionHotkey();
  await stopVoiceAndTts();
  await postCompanion("/app/shutdown", 3000);
  await killPythonProcessTree();
  await killWebProcessTree();
  await killStaleBackendOnPort(BACKEND_PORT);
  await killStaleBackendOnPort(WEB_PORT);
  await stopOllama();
  app.exit(0);
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    if (pythonProcess) {
      logToFile("Stopping backend...");
      pythonProcess.kill("SIGINT");
    }
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
