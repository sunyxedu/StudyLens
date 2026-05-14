import { DEFAULT_SETTINGS } from "./storage.js";

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get({ ...DEFAULT_SETTINGS });
  if (!stored.backendUrl) {
    await chrome.storage.local.set({ ...DEFAULT_SETTINGS });
  }
});
