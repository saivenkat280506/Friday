const { app, BrowserWindow, dialog } = require("electron");
const path = require("path");
const { spawn } = require("child_process");
const http = require("http");
const net = require("net");
const fs = require("fs");

const PROJECT_ROOT = path.join(__dirname, "../..");
const BACKEND_DIR = path.join(PROJECT_ROOT, "backend");
const BACKEND_PORT = 8000;
const WEB_URL = "http://localhost:3000";

let mainWindow;
let pythonProcess;
let overlayWindow;

const OVERLAY_HTML = `<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;background:transparent;overflow:hidden}body{width:104px;height:104px;display:grid;place-items:center;font-family:Segoe UI,sans-serif}.shell{width:80px;height:80px;border-radius:28px;background:rgba(5,7,16,.72);border:1px solid rgba(161,112,255,.42);display:grid;place-items:center;box-shadow:0 12px 44px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.12);backdrop-filter:blur(18px)}.orb{width:46px;height:46px;border-radius:50%;background:radial-gradient(circle at 32% 28%,#fff 0 3%,#bc8cff 12%,#5523aa 35%,#12102e 69%);box-shadow:0 0 0 3px rgba(166,113,255,.14),0 0 22px rgba(138,77,255,.85);animation:idle 3s ease-in-out infinite}.label{position:fixed;bottom:0;color:#d9cbff;font-size:9px;font-weight:700;letter-spacing:.17em;text-transform:uppercase;text-shadow:0 1px 7px #000}body[data-state="listening"] .orb{animation:listening .72s ease-in-out infinite}body[data-state="thinking"] .orb,body[data-state="transcribing"] .orb{animation:thinking 1.2s linear infinite}body[data-state="talking"] .orb{animation:talking .35s ease-in-out infinite alternate}@keyframes idle{50%{transform:scale(1.08);box-shadow:0 0 0 6px rgba(166,113,255,.08),0 0 26px rgba(138,77,255,.6)}}@keyframes listening{50%{transform:scale(1.25);box-shadow:0 0 0 12px rgba(89,219,255,.12),0 0 30px #43c8ff}}@keyframes thinking{to{transform:rotate(360deg)}}@keyframes talking{to{transform:scale(1.18,.88);filter:brightness(1.32)}}
</style></head><body data-state="idle"><div class="shell"><div class="orb"></div></div><div class="label">Friday</div><script>const labels={idle:'Friday',listening:'Listening',thinking:'Thinking',transcribing:'Hearing',talking:'Speaking'};async function update(){try{const r=await fetch('http://127.0.0.1:8000/health',{cache:'no-store'});const d=await r.json();const state=d.state||'idle';document.body.dataset.state=state;document.querySelector('.label').textContent=labels[state]||'Friday'}catch{document.body.dataset.state='idle';document.querySelector('.label').textContent='Offline'}}update();setInterval(update,500);</script></body></html>`;

function logToFile(msg, isError = false) {
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
      resolve(res.statusCode === 200);
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
          `Port ${BACKEND_PORT} in use but unhealthy. Run: npm run stop:desktop`
        );
        return reject(
          new Error(
            `Port ${BACKEND_PORT} is occupied by a stale process.\n\nRun: npm run stop:desktop\nThen restart FRIDAY.`
          )
        );
      }

      const pythonPath = path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe");
      const scriptPath = path.join(BACKEND_DIR, "main.py");

      if (!fs.existsSync(pythonPath)) {
        return reject(
          new Error(
            `Python virtual environment not found.\n\nExpected:\n${pythonPath}\n\nRun: cd backend && python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt`
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

async function createWindow() {
  try {
    await startBackend();
  } catch (err) {
    dialog.showErrorBox("FRIDAY Backend Startup Failure", err.message);
    app.quit();
    return;
  }

  const iconPath = path.join(PROJECT_ROOT, "friday_icon.png");

  mainWindow = new BrowserWindow({
    width: 1300,
    height: 850,
    minWidth: 1000,
    minHeight: 650,
    autoHideMenuBar: true,
    title: "F.R.I.D.A.Y.",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: false,
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
    mainWindow.show();
  });

  mainWindow.on("minimize", () => {
    if (overlayWindow && !overlayWindow.isDestroyed()) overlayWindow.showInactive();
  });

  mainWindow.on("restore", () => {
    if (overlayWindow && !overlayWindow.isDestroyed()) overlayWindow.hide();
  });

  mainWindow.on("closed", () => {
    if (overlayWindow && !overlayWindow.isDestroyed()) overlayWindow.close();
    mainWindow = null;
  });
}

function createOverlayWindow() {
  const { screen } = require("electron");
  const workArea = screen.getPrimaryDisplay().workArea;
  overlayWindow = new BrowserWindow({
    width: 104,
    height: 104,
    x: Math.round(workArea.x + (workArea.width - 104) / 2),
    y: workArea.y + 12,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.setIgnoreMouseEvents(true, { forward: true });
  overlayWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(OVERLAY_HTML)}`);
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

app.whenReady().then(async () => {
  createOverlayWindow();
  await createWindow();
});

app.on("window-all-closed", () => {
  if (pythonProcess) {
    logToFile("Stopping backend...");
    pythonProcess.kill("SIGINT");
  }

  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
