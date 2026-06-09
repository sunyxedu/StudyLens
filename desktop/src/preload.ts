import { contextBridge, ipcRenderer } from "electron";

type BrowserSetupResult = {
  ok: boolean;
  message: string;
};

const desktopApi = {
  isDesktop: true,
  platform: process.platform,
  version: process.env.npm_package_version ?? "0.0.0",
  async startBrowserSetup(): Promise<BrowserSetupResult> {
    return ipcRenderer.invoke("browser-setup:start") as Promise<BrowserSetupResult>;
  },
};

contextBridge.exposeInMainWorld("studylensDesktop", desktopApi);
