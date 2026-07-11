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

const OVERLAY_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;background:transparent;overflow:hidden}body{width:286px;height:132px;padding:8px;font-family:"Segoe UI Variable","Segoe UI",sans-serif;color:#f8f6ff}.companion{width:270px;height:116px;padding:14px 16px;overflow:hidden;border:1px solid rgba(189,160,255,.32);border-radius:27px;background:#0b0b17;transition:border-color .35s ease,background-color .35s ease}.top,.content{display:flex;align-items:center}.top{justify-content:space-between;height:25px}.brand{font-size:10px;font-weight:750;letter-spacing:.18em;color:#ddd2ff;text-transform:uppercase}.status{display:flex;align-items:center;gap:6px;font-size:10px;font-weight:650;letter-spacing:.055em;color:#a9a4b9}.status-dot{width:6px;height:6px;border-radius:50%;background:#9382b5;box-shadow:0 0 0 3px rgba(148,129,181,.13)}.content{gap:11px;margin-top:11px}.orb-frame{display:grid;flex:0 0 50px;width:50px;height:50px;place-items:center;border:1px solid rgba(196,175,255,.38);border-radius:18px;background:#090914}.orb{width:31px;height:31px;border-radius:50%;background:radial-gradient(circle at 32% 28%,#fff 0 4%,#d7c7ff 8%,#985cff 30%,#43218d 53%,#17132f 72%);box-shadow:0 0 0 3px rgba(166,113,255,.14),0 0 17px rgba(142,79,255,.72)}.copy{min-width:0;flex:1}.headline{overflow:hidden;font-size:13px;font-weight:700;line-height:1.2;white-space:nowrap;text-overflow:ellipsis}.detail{margin-top:5px;font-size:10px;line-height:1.25;color:#aaa5b7}.meter{display:flex;align-items:center;gap:3px;width:30px;height:18px;margin-left:3px}.meter i{display:block;width:3px;height:4px;border-radius:3px;background:#8d78c8}.meter i:nth-child(2){height:9px}.meter i:nth-child(3){height:13px}.meter i:nth-child(4){height:8px}.meter i:nth-child(5){height:5px}body[data-state="listening"] .companion{border-color:rgba(92,224,255,.6);animation:listen-card 1.96s ease-in-out infinite}body[data-state="listening"] .status-dot{background:#57dcff;box-shadow:0 0 0 4px rgba(87,220,255,.13),0 0 12px #57dcff}body[data-state="listening"] .orb{animation:listen-orb 1.96s ease-in-out infinite}body[data-state="listening"] .meter i{background:#5ce0ff;animation:levels .65s ease-in-out infinite alternate}body[data-state="listening"] .meter i:nth-child(2){animation-delay:.12s}body[data-state="listening"] .meter i:nth-child(3){animation-delay:.24s}body[data-state="thinking"] .companion,body[data-state="transcribing"] .companion{border-color:rgba(183,151,255,.62);animation:process-card 8.9s ease-in-out infinite}body[data-state="thinking"] .status-dot,body[data-state="transcribing"] .status-dot{background:#b797ff;box-shadow:0 0 0 4px rgba(183,151,255,.13)}body[data-state="thinking"] .orb,body[data-state="transcribing"] .orb{animation:process-orb 8.9s linear infinite}body[data-state="thinking"] .meter i,body[data-state="transcribing"] .meter i{background:#b797ff;animation:levels 1.25s ease-in-out infinite alternate}body[data-state="talking"] .companion{border-color:rgba(209,154,255,.64);animation:speak-card 1.3s ease-in-out infinite alternate}body[data-state="talking"] .status-dot{background:#d19aff;box-shadow:0 0 0 4px rgba(209,154,255,.13),0 0 12px #d19aff}body[data-state="talking"] .orb{animation:speak-orb 1.3s ease-in-out infinite alternate}body[data-state="talking"] .meter i{background:#d19aff;animation:levels .42s ease-in-out infinite alternate}@keyframes listen-card{50%{transform:translateY(-1px);background:#0d1020}}@keyframes listen-orb{50%{transform:scale(1.12);box-shadow:0 0 0 7px rgba(87,220,255,.1),0 0 23px rgba(87,220,255,.86)}}@keyframes process-card{50%{transform:translateY(-1px);background:#0e0c1c}}@keyframes process-orb{to{transform:rotate(360deg)}}@keyframes speak-card{to{transform:translateY(-1px);background:#100c1d}}@keyframes speak-orb{to{transform:scale(1.12,.9);filter:brightness(1.2)}}@keyframes levels{to{transform:scaleY(.45)}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important}}
</style></head><body data-state="idle"><section class="companion" aria-label="Friday companion"><div class="top"><div class="brand">F.R.I.D.A.Y.</div><div class="status"><span class="status-dot"></span><span class="status-label">Ready</span></div></div><div class="content"><div class="orb-frame"><div class="orb"></div></div><div class="copy"><div class="headline">How can I help?</div><div class="detail">Voice assistant is standing by</div></div><div class="meter" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div></div></section><script>const content={idle:['Ready','How can I help?','Voice assistant is standing by'],listening:['Listening','I\'m listening…','Speak naturally'],thinking:['Thinking','Working on that…','One moment'],transcribing:['Hearing','Capturing your words…','Almost there'],talking:['Speaking','Here\'s what I found','FRIDAY is responding'],offline:['Offline','FRIDAY is reconnecting','The local service is unavailable']};let reconnectTimer;function setState(next){const state=next==='idle_listening'?'listening':next;const value=content[state]||content.idle;document.body.dataset.state=state;document.querySelector('.status-label').textContent=value[0];document.querySelector('.headline').textContent=value[1];document.querySelector('.detail').textContent=value[2]}async function syncFromHealth(){try{const r=await fetch('http://127.0.0.1:8000/health',{cache:'no-store'});if(!r.ok)throw new Error('Unavailable');const d=await r.json();setState(d.state||'idle')}catch{setState('offline')}}function connect(){const ws=new WebSocket('ws://127.0.0.1:8000/ws');ws.onmessage=event=>{try{const data=JSON.parse(event.data);if(data.state)setState(data.state);if(data.type==='tts_started')setState('talking')}catch{}};ws.onopen=syncFromHealth;ws.onclose=()=>{syncFromHealth();clearTimeout(reconnectTimer);reconnectTimer=setTimeout(connect,1500)};ws.onerror=()=>ws.close()}syncFromHealth();connect();setInterval(syncFromHealth,10000);</script></body></html>`;

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
    width: 286,
    height: 132,
    x: Math.round(workArea.x + (workArea.width - 286) / 2),
    y: workArea.y + 16,
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
