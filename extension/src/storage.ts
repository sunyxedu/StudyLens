import type { StudyLensSettings } from "./types.js";

export const DEFAULT_SETTINGS: StudyLensSettings = {
  backendUrl: "http://localhost:8000",
};

export async function loadSettings(): Promise<StudyLensSettings> {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return DEFAULT_SETTINGS;
  }
  const stored = await chrome.storage.local.get({ ...DEFAULT_SETTINGS });
  return {
    backendUrl:
      typeof stored.backendUrl === "string" && stored.backendUrl.trim()
        ? stored.backendUrl
        : DEFAULT_SETTINGS.backendUrl,
  };
}

export async function saveSettings(settings: StudyLensSettings): Promise<void> {
  if (typeof chrome === "undefined" || !chrome.storage?.local) {
    return;
  }
  await chrome.storage.local.set({ ...settings });
}
