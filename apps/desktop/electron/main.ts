import { app, BrowserWindow, globalShortcut, screen, ipcMain } from "electron";
import path from "path";
import fs from "fs";
import http from "http";
import { spawn, ChildProcess } from "child_process";

const logFile = path.join(__dirname, "../electron_runtime.log");
function log(msg: string) {
  try {
    fs.appendFileSync(logFile, `[${new Date().toISOString()}] ${msg}\n`);
  } catch {}
}

log("=== Electron Main Process Booting ===");

const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  log("Another instance is already running. Quitting duplicate instance.");
  app.quit();
} else {
  app.on("second-instance", () => {
    log("second-instance triggered: focusing Sidecar window");
    if (sidecarWindow && !sidecarWindow.isDestroyed()) {
      if (sidecarWindow.isMinimized()) sidecarWindow.restore();
      sidecarWindow.show();
      sidecarWindow.focus();
    }
  });
}

let mainWindow: BrowserWindow | null = null;
let sidecarWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let isSidecarCollapsed = false;

const DEV_URL = process.env.RALLY_DESKTOP_DEV_URL || "http://127.0.0.1:5173";
const API_BASE = "http://127.0.0.1:8765/api/v1";

function getDistPath(): string {
  return path.join(__dirname, "../dist/index.html");
}

function isUrlReachable(urlStr: string): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      const req = http.get(urlStr, { timeout: 600 }, (res) => {
        res.resume();
        resolve(res.statusCode ? res.statusCode < 500 : false);
      });
      req.on("error", () => resolve(false));
      req.on("timeout", () => {
        req.destroy();
        resolve(false);
      });
    } catch {
      resolve(false);
    }
  });
}

function getOrchestratorPaths(): { pyExe: string; cwd: string } | null {
  const candidates = [
    path.resolve(__dirname, "../../../services/orchestrator"),
    path.resolve(__dirname, "../../services/orchestrator"),
    path.resolve(process.cwd(), "services/orchestrator"),
  ];
  for (const dir of candidates) {
    const py = path.join(dir, ".venv/Scripts/python.exe");
    if (fs.existsSync(py)) {
      return { pyExe: py, cwd: dir };
    }
  }
  return null;
}

function getSecurityCredentials(): { apiToken: string; approvalSecret: string } {
  const orchestrator = getOrchestratorPaths();
  if (orchestrator) {
    const tokenPath = path.join(orchestrator.cwd, "data/.rally_token");
    const secretPath = path.join(orchestrator.cwd, "data/.rally_approval_secret");
    const apiToken = fs.existsSync(tokenPath) ? fs.readFileSync(tokenPath, "utf-8").trim() : "";
    const approvalSecret = fs.existsSync(secretPath) ? fs.readFileSync(secretPath, "utf-8").trim() : "";
    return { apiToken, approvalSecret };
  }
  return { apiToken: "", approvalSecret: "" };
}

function postApi(endpoint: string): Promise<void> {
  const { apiToken } = getSecurityCredentials();
  return new Promise((resolve) => {
    try {
      const headers: Record<string, string> = {
        Host: "127.0.0.1:8765",
      };
      if (apiToken) {
        headers["Authorization"] = `Bearer ${apiToken}`;
      }
      const req = http.request(`http://127.0.0.1:8765/api/v1${endpoint}`, { method: "POST", headers }, (res) => {
        res.resume();
        log(`POST ${endpoint} -> ${res.statusCode}`);
        resolve();
      });
      req.on("error", (err) => {
        log(`POST ${endpoint} error: ${err.message}`);
        resolve();
      });
      req.end();
    } catch {
      resolve();
    }
  });
}

async function ensureBackendRunning() {
  const isUp = await isUrlReachable(`${API_BASE}/health`);
  if (!isUp) {
    const target = getOrchestratorPaths();
    if (target) {
      log(`Spawning backend: ${target.pyExe}`);
      backendProcess = spawn(target.pyExe, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765"], {
        cwd: target.cwd,
        stdio: "ignore",
        windowsHide: true,
      });
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 250));
        if (await isUrlReachable(`${API_BASE}/health`)) {
          log("Backend came online.");
          break;
        }
      }
    }
  }
}

async function loadAppWindow(win: BrowserWindow, searchParams = "") {
  const isViteUp = await isUrlReachable(DEV_URL);
  if (isViteUp) {
    log(`Loading Vite DEV_URL: ${DEV_URL}`);
    win.loadURL(`${DEV_URL}${searchParams ? `?${searchParams}` : ""}`);
  } else {
    const distIndex = getDistPath();
    if (fs.existsSync(distIndex)) {
      log(`Loading distIndex: ${distIndex}`);
      win.loadFile(distIndex, searchParams ? { search: searchParams } : {});
    } else {
      log(`Fallback loading DEV_URL`);
      win.loadURL(`${DEV_URL}${searchParams ? `?${searchParams}` : ""}`);
    }
  }
}

function createMainWindow(): BrowserWindow {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    mainWindow.focus();
    return mainWindow;
  }

  log("Creating mainWindow (Mission Control)...");
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 880,
    backgroundColor: "#0a0d14",
    title: "RALLY 2.0 — Mission Control & Build Supervisor",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  loadAppWindow(mainWindow);
  return mainWindow;
}

function getExpandedBounds() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;
  const targetHeight = Math.min(680, height - 30);
  const targetWidth = 380;
  return {
    x: width - targetWidth - 10,
    y: Math.max(15, Math.round((height - targetHeight) / 2)),
    width: targetWidth,
    height: targetHeight,
  };
}

function getCollapsedBounds() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { width, height } = primaryDisplay.workAreaSize;
  return {
    x: width - 72,
    y: Math.round(height / 2) - 45,
    width: 66,
    height: 90,
  };
}

function ensureSidecarWindow(): BrowserWindow {
  if (!sidecarWindow || sidecarWindow.isDestroyed()) {
    const bounds = getExpandedBounds();
    log(`Creating Sidecar Agent window: ${JSON.stringify(bounds)}`);
    sidecarWindow = new BrowserWindow({
      ...bounds,
      alwaysOnTop: true,
      frame: false,
      transparent: false,
      resizable: true,
      hasShadow: true,
      skipTaskbar: false,
      backgroundColor: "#070a12",
      title: "RALLY 2.0 Sidecar Agent",
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });

    sidecarWindow.setAlwaysOnTop(true, "floating");
    sidecarWindow.setVisibleOnAllWorkspaces(true);
    loadAppWindow(sidecarWindow, "sidecar=1&companion=1");
  }
  return sidecarWindow;
}

function toggleSidecarWindow() {
  log("toggleSidecarWindow triggered");
  const win = ensureSidecarWindow();
  if (win.isVisible()) {
    win.hide();
  } else {
    win.show();
    win.focus();
  }
}

async function activateLiveVoice() {
  log(">>> HOTKEY / WAKE EVENT TRIGGERED <<<");
  const win = ensureSidecarWindow();
  if (isSidecarCollapsed) {
    expandSidecar();
  }
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
  win.webContents.send("voice:wake");
  await postApi("/voice/wake");
}

function collapseSidecar() {
  if (sidecarWindow && !sidecarWindow.isDestroyed()) {
    const b = getCollapsedBounds();
    sidecarWindow.setBounds(b);
    isSidecarCollapsed = true;
    sidecarWindow.webContents.send("sidecar:state", { collapsed: true });
    log("Sidecar collapsed into orb");
  }
}

function expandSidecar() {
  if (sidecarWindow && !sidecarWindow.isDestroyed()) {
    const b = getExpandedBounds();
    sidecarWindow.setBounds(b);
    isSidecarCollapsed = false;
    sidecarWindow.webContents.send("sidecar:state", { collapsed: false });
    log("Sidecar expanded into full panel");
  }
}

// IPC Handlers
ipcMain.handle("security:get-tokens", () => {
  return getSecurityCredentials();
});
ipcMain.on("sidecar:collapse", () => collapseSidecar());
ipcMain.on("sidecar:expand", () => expandSidecar());
ipcMain.on("sidecar:toggle", () => {
  if (isSidecarCollapsed) expandSidecar();
  else collapseSidecar();
});
ipcMain.on("dashboard:open", () => {
  createMainWindow();
});
ipcMain.on("voice:wake", () => {
  activateLiveVoice();
});

app.whenReady().then(() => {
  log("app.whenReady resolved — Instant window initialization");

  // Show the Sidecar Agent on the right edge IMMEDIATELY
  const sidecar = ensureSidecarWindow();
  sidecar.show();
  sidecar.focus();
  log("Sidecar Agent displayed on screen immediately");

  // Show main window
  createMainWindow();
  log("Main window initialized");

  // Register Global Hotkeys immediately
  const hotkeys = [
    { key: "CommandOrControl+Shift+Space", fn: activateLiveVoice },
    { key: "CommandOrControl+Space", fn: activateLiveVoice },
    { key: "Alt+Space", fn: activateLiveVoice },
    { key: "CommandOrControl+Shift+A", fn: activateLiveVoice },
    { key: "CommandOrControl+Shift+V", fn: activateLiveVoice },
    { key: "F9", fn: activateLiveVoice },
    { key: "F8", fn: activateLiveVoice },
    { key: "F7", fn: activateLiveVoice },
    { key: "CommandOrControl+Shift+R", fn: toggleSidecarWindow },
    { key: "CommandOrControl+Shift+X", fn: () => postApi("/desktop/stop") },
  ];

  for (const h of hotkeys) {
    try {
      const ok = globalShortcut.register(h.key, h.fn);
      log(`Hotkey [${h.key}]: ${ok ? "ACTIVE" : "OCCUPIED"}`);
    } catch (e: any) {
      log(`Hotkey [${h.key}] error: ${e.message}`);
    }
  }

  // Ensure backend is running in background without blocking UI
  ensureBackendRunning().then(() => {
    postApi("/voice/start");
  });
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  if (backendProcess) {
    try {
      backendProcess.kill();
    } catch {}
  }
});
