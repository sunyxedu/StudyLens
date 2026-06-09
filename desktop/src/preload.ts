import { contextBridge } from "electron";

type BrowserSetupResult = {
  ok: boolean;
  message: string;
};

const desktopApi = {
  isDesktop: true,
  platform: process.platform,
  version: process.env.npm_package_version ?? "0.0.0",
  async startBrowserSetup(): Promise<BrowserSetupResult> {
    return {
      ok: false,
      message: "Desktop browser setup is not implemented yet.",
    };
  },
};

contextBridge.exposeInMainWorld("studylensDesktop", desktopApi);
