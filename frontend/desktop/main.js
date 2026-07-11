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

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
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

app.whenReady().then(createWindow);

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