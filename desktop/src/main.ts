import { app, BrowserWindow } from "electron";

const STUDYLENS_URL =
  process.env.STUDYLENS_URL ?? "https://www.google.com/";

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  void window.loadURL(STUDYLENS_URL);
  // Simple wrapper for web app for now
}

app.whenReady().then(createWindow);

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