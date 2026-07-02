// Paper Radar desktop (Electron) main process.
//
// Responsibilities:
//   1. Spawn the FastAPI backend (dev: repo .venv / python -m uvicorn,
//      prod: bundled PyInstaller binary in resources).
//   2. Wait for the backend /api/health endpoint to answer.
//   3. Create the BrowserWindow (dev: Vite dev server, prod: built dist).
//   4. Tear the backend down cleanly on quit.
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("node:child_process");
const http = require("node:http");
const path = require("node:path");
const fs = require("node:fs");

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = Number(process.env.PAPER_RADAR_BACKEND_PORT || 8000);
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
const DEV_SERVER_URL = process.env.PAPER_RADAR_DEV_SERVER || "http://127.0.0.1:5173";

// frontend/electron/main.cjs -> repo root is two levels up.
const REPO_ROOT = path.resolve(__dirname, "..", "..");

let backendProcess = null;
let mainWindow = null;

function log(...args) {
  console.log("[paper-radar]", ...args);
}

// The self-use packaged .app records this machine's repo + venv paths at build
// time (see write-local-config.cjs). In dev we derive them relative to source.
function loadLocalConfig() {
  try {
    return require("./local-paths.json");
  } catch {
    return null;
  }
}

function resolvePython(repoRoot) {
  const candidates = [
    path.join(repoRoot, ".venv", "bin", "python"),
    path.join(repoRoot, ".venv", "Scripts", "python.exe"),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return process.platform === "win32" ? "python" : "python3";
}

function startBackend() {
  // Both dev and the self-use build run the backend from the repo's own .venv
  // and reuse the repo's data/ directory (so the existing sqlite + figure cache
  // are picked up). PAPER_RADAR_DB drives both paths (see backend/app/db.py).
  const cfg = loadLocalConfig();
  const repoRoot = (cfg && cfg.repoRoot) || REPO_ROOT;
  const python = cfg && cfg.python && fs.existsSync(cfg.python) ? cfg.python : resolvePython(repoRoot);

  const dataDir = path.join(repoRoot, "data");
  fs.mkdirSync(dataDir, { recursive: true });

  const env = {
    ...process.env,
    PAPER_RADAR_DB: path.join(dataDir, "paper_radar.sqlite"),
    PAPER_RADAR_BACKEND_HOST: BACKEND_HOST,
    PAPER_RADAR_BACKEND_PORT: String(BACKEND_PORT),
  };

  log("starting backend:", python, "@", BACKEND_URL, "(repo:", repoRoot + ")");
  backendProcess = spawn(
    python,
    ["-m", "uvicorn", "backend.app.main:app", "--host", BACKEND_HOST, "--port", String(BACKEND_PORT)],
    { cwd: repoRoot, env }
  );

  backendProcess.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backendProcess.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backendProcess.on("exit", (code, signal) => {
    log(`backend exited (code=${code}, signal=${signal})`);
    backendProcess = null;
  });
}

function stopBackend() {
  if (!backendProcess) return;
  log("stopping backend");
  try {
    backendProcess.kill(process.platform === "win32" ? undefined : "SIGTERM");
  } catch (err) {
    log("failed to stop backend:", err);
  }
  backendProcess = null;
}

function waitForBackend(timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(`${BACKEND_URL}/api/health`, (res) => {
        res.resume();
        if (res.statusCode && res.statusCode < 500) {
          resolve();
        } else {
          retry();
        }
      });
      req.on("error", retry);
      req.setTimeout(2000, () => req.destroy());
    };
    const retry = () => {
      if (Date.now() > deadline) {
        reject(new Error("backend did not become healthy in time"));
        return;
      }
      setTimeout(attempt, 400);
    };
    attempt();
  });
}

function createWindow() {
  const isMac = process.platform === "darwin";
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 900,
    minHeight: 640,
    title: "Paper Radar",
    // Native macOS chrome: hide the title bar, inline the traffic lights, and
    // frost the transparent (sidebar) region with vibrancy.
    titleBarStyle: isMac ? "hiddenInset" : "default",
    trafficLightPosition: isMac ? { x: 16, y: 20 } : undefined,
    vibrancy: isMac ? "sidebar" : undefined,
    visualEffectState: "active",
    backgroundColor: isMac ? "#00000000" : "#0f1115",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--paper-radar-api-base=${BACKEND_URL}`],
    },
  });

  // Open external links in the system browser, keep app navigation in-window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  if (app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    mainWindow.loadURL(DEV_SERVER_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  startBackend();
  try {
    await waitForBackend();
  } catch (err) {
    log("warning:", err.message, "- loading UI anyway");
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopBackend);
process.on("exit", stopBackend);
