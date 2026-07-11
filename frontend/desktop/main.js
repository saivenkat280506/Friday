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

let OVERLAY_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;background:transparent;overflow:hidden}body{width:238px;height:168px;padding:8px;font-family:"Segoe UI Variable","Segoe UI",sans-serif;color:#f6f5f9}.widget{width:222px;height:152px;overflow:hidden;border:1px solid #272730;border-radius:25px;background:#0a0a0d;transition:border-color .3s ease,background-color .3s ease}.widget-top{display:flex;align-items:center;justify-content:space-between;height:57px;padding:10px 13px}.mini-orb{display:grid;width:38px;height:38px;place-items:center;border-radius:50%;background:conic-gradient(from 215deg,#23104e,#7a3dff 20%,#d7bbff 35%,#5523c5 56%,transparent 73%,#29135b 88%,#23104e)}.mini-orb:after{content:"";width:30px;height:30px;border-radius:50%;background:#100b1c}.control-capsule{display:flex;align-items:center;justify-content:space-evenly;width:84px;height:34px;border:1px solid #282832;border-radius:18px;background:#050507}.bars{display:flex;align-items:center;gap:2px;height:16px}.bars i{width:2px;border-radius:4px;background:#d8d5df}.bars i:nth-child(1){height:4px}.bars i:nth-child(2){height:9px}.bars i:nth-child(3){height:13px}.bars i:nth-child(4){height:7px}.mic{width:14px;height:14px;fill:none;stroke:#d8d5df;stroke-linecap:round;stroke-linejoin:round;stroke-width:1.8}.task-panel{height:95px;padding:11px 13px;border-top:1px solid #1b1b22}.task-row{display:flex;align-items:center;gap:9px}.task-mark{display:grid;width:30px;height:30px;place-items:center;border-radius:50%;background:radial-gradient(circle at 35% 30%,#9182ff 0 7%,#5b37bf 27%,#24114d 64%,#0d0c14 100%)}.task-mark:after{content:"";width:14px;height:14px;border:1px solid #d4c6ff;border-radius:50%}.task-copy{min-width:0;flex:1}.task-label{overflow:hidden;color:#a6a3ae;font-size:9px;font-weight:700;letter-spacing:.05em;white-space:nowrap;text-overflow:ellipsis}.task-status{margin-top:2px;font-size:19px;font-weight:720;line-height:1;letter-spacing:-.035em}.task-detail{margin-top:5px;color:#9d9aa5;font-size:9px}.activity-track{position:relative;height:14px;margin-top:8px;overflow:hidden}.activity-track:before{content:"";position:absolute;top:7px;left:0;right:0;height:1px;background:#302c3d}.activity-line{position:absolute;top:1px;left:0;width:100%;height:13px;fill:none;stroke:#8157ed;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;opacity:.78}body[data-state="listening"] .widget{border-color:#3a6a79;animation:widget-listen 1.96s ease-in-out infinite}body[data-state="listening"] .mini-orb{animation:ring-listen 1.96s ease-in-out infinite}body[data-state="listening"] .bars i{animation:bars .65s ease-in-out infinite alternate}body[data-state="thinking"] .widget,body[data-state="transcribing"] .widget{border-color:#49366e;animation:widget-work 2.8s ease-in-out infinite}body[data-state="thinking"] .mini-orb,body[data-state="transcribing"] .mini-orb{animation:ring-work 2.8s linear infinite}body[data-state="thinking"] .activity-line,body[data-state="transcribing"] .activity-line{animation:scan 1.2s linear infinite}body[data-state="thinking"] .bars i,body[data-state="transcribing"] .bars i{animation:bars 1.05s ease-in-out infinite alternate}body[data-state="talking"] .widget{border-color:#6a3d76;animation:widget-talk 1.3s ease-in-out infinite alternate}body[data-state="talking"] .mini-orb{animation:ring-talk 1.3s ease-in-out infinite alternate}body[data-state="talking"] .bars i{animation:bars .42s ease-in-out infinite alternate}@keyframes widget-listen{50%{transform:translateY(-1px);background:#0b0d12}}@keyframes widget-work{50%{transform:translateY(-1px);background:#0c0a11}}@keyframes widget-talk{to{transform:translateY(-1px);background:#0d0a12}}@keyframes ring-listen{50%{transform:scale(1.08) rotate(12deg)}}@keyframes ring-work{to{transform:rotate(360deg)}}@keyframes ring-talk{to{transform:scale(1.1,.92) rotate(18deg)}}@keyframes bars{to{transform:scaleY(.38)}}@keyframes scan{50%{transform:translateX(5px);opacity:1}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important}}
</style></head><body data-state="idle"><section class="widget" aria-label="FRIDAY companion"><div class="widget-top"><div class="mini-orb" aria-hidden="true"></div><div class="control-capsule" aria-label="Voice controls"><div class="bars" aria-hidden="true"><i></i><i></i><i></i><i></i></div><svg class="mic" viewBox="0 0 16 16" aria-hidden="true"><rect x="5.4" y="1.4" width="5.2" height="8.2" rx="2.6"></rect><path d="M3.4 7.5a4.6 4.6 0 0 0 9.2 0M8 12.1v2.2M5.6 14.3h4.8"></path></svg></div></div><div class="task-panel"><div class="task-row"><div class="task-mark"></div><div class="task-copy"><div class="task-label">FRIDAY CORE</div><div class="task-status">Ready</div><div class="task-detail">Voice interface online</div></div></div><div class="activity-track"><svg class="activity-line" viewBox="0 0 194 14" preserveAspectRatio="none"><path d="M0 10 L17 10 L27 7 L43 10 L61 9 L74 3 L86 10 L105 10 L120 6 L132 10 L149 8 L161 10 L179 4 L194 9"></path></svg></div></div></section><script>const content={idle:['Ready','Voice interface online'],listening:['Listening','Capturing your words'],thinking:['Working','Processing your task'],transcribing:['Hearing','Transcribing your request'],talking:['Responding','FRIDAY is speaking'],offline:['Offline','Local service unavailable']};let reconnectTimer;function setState(next){const state=next==='idle_listening'?'listening':next;const value=content[state]||content.idle;document.body.dataset.state=state;document.querySelector('.task-status').textContent=value[0];document.querySelector('.task-detail').textContent=value[1]}async function syncFromHealth(){try{const r=await fetch('http://127.0.0.1:8000/health',{cache:'no-store'});if(!r.ok)throw new Error('Unavailable');const d=await r.json();setState(d.state||'idle')}catch{setState('offline')}}function connect(){const ws=new WebSocket('ws://127.0.0.1:8000/ws');ws.onmessage=event=>{try{const data=JSON.parse(event.data);if(data.state)setState(data.state);if(data.type==='tts_started')setState('talking')}catch{}};ws.onopen=syncFromHealth;ws.onclose=()=>{syncFromHealth();clearTimeout(reconnectTimer);reconnectTimer=setTimeout(connect,1500)};ws.onerror=()=>ws.close()}syncFromHealth();connect();setInterval(syncFromHealth,10000);</script></body></html>`;

OVERLAY_HTML = OVERLAY_HTML
  .replace(
    "</head>",
    `<style>.control-capsule,.task-panel{cursor:pointer}.control-capsule:hover{border-color:#7a52db}.task-panel:hover{background:#0e0e14}.control-capsule:focus,.task-panel:focus{outline:1px solid #9b74f7;outline-offset:-3px}body[data-state="idle"] .mini-orb{animation:ring-idle 5.2s ease-in-out infinite}body[data-state="idle"] .activity-line{animation:trace-idle 3.6s ease-in-out infinite}body[data-state="idle"] .bars i{animation:bars 2.2s ease-in-out infinite alternate}@keyframes ring-idle{50%{transform:scale(1.045) rotate(12deg)}}@keyframes trace-idle{50%{transform:translateX(2px);opacity:1}}@media(prefers-reduced-motion:reduce){.control-capsule,.task-panel{transition:none}}</style></head>`
  )
  .replace(
    "</body>",
    `<script>const micControl=document.querySelector('.control-capsule');const taskPanel=document.querySelector('.task-panel');function activateVoice(){setState('listening');fetch('http://127.0.0.1:8000/listen-trigger',{method:'POST'}).catch(()=>setState('offline'))}function restoreFriday(){window.location.assign('friday://restore')}micControl.tabIndex=0;micControl.setAttribute('role','button');micControl.addEventListener('click',activateVoice);micControl.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();activateVoice()}});taskPanel.tabIndex=0;taskPanel.setAttribute('role','button');taskPanel.addEventListener('click',restoreFriday);taskPanel.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();restoreFriday()}});</script></body>`
  );

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
    width: 238,
    height: 168,
    x: Math.round(workArea.x + (workArea.width - 238) / 2),
    y: workArea.y + 16,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    focusable: true,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: false,
    show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
  });
  overlayWindow.setAlwaysOnTop(true, "screen-saver");
  overlayWindow.webContents.on("will-navigate", (event, url) => {
    if (url !== "friday://restore") return;
    event.preventDefault();
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
    overlayWindow.hide();
  });
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
