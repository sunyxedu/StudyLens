import { app, BrowserWindow, ipcMain } from "electron";
import path from "node:path";
import { captureAndUploadBrowserState } from "./capture.js";

const STUDYLENS_URL =
  process.env.STUDYLENS_URL ?? "https://www.google.com/"; // So that error is observable

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js")
    }
  });

  void window.loadURL(STUDYLENS_URL);
  // Simple wrapper for web app for now
}

app.whenReady().then(() => {
  ipcMain.handle("browser-setup:start", async (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) {
      return { ok: false, message: "Could not find the StudyLens desktop window." };
    }

    try {
      await captureAndUploadBrowserState(window);
      return { ok: true, message: "Academic site logins connected." };
    } catch (error) {
      return {
        ok: false,
        message: error instanceof Error ? error.message : "Browser setup failed.",
      };
    }
  });

  createWindow();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
