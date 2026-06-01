export interface AppSettings {
  backendUrl: string;
}

const STORAGE_KEY = "studylens.web.settings";
const FALLBACK_BACKEND_URL = "http://localhost:8000";

export const DEFAULT_SETTINGS: AppSettings = {
  backendUrl: configuredBackendUrl(),
};

export function loadSettings(storage: Storage = localStorage): AppSettings {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    return {
      backendUrl: stringOrDefault(parsed.backendUrl, DEFAULT_SETTINGS.backendUrl),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: AppSettings, storage: Storage = localStorage): void {
  storage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

export function parseScopeNotes(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function resolveBackendUrl(settings: AppSettings, location: Location): string {
  const isBundledApp = location.pathname.startsWith("/app");
  if (isBundledApp && settings.backendUrl === DEFAULT_SETTINGS.backendUrl) {
    return location.origin;
  }
  if (isLocalDevFrontend(location) && isLocalDevBackend(settings.backendUrl)) {
    return `${location.protocol}//${hostForUrl(location.hostname)}:8000`;
  }
  return settings.backendUrl;
}

export function sanitizeFilename(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return normalized || "studylens";
}

function stringOrDefault(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function configuredBackendUrl(): string {
  const configured = (globalThis as { STUDYLENS_BACKEND_URL?: unknown }).STUDYLENS_BACKEND_URL;
  return stringOrDefault(configured, FALLBACK_BACKEND_URL);
}

function isLocalDevFrontend(location: Location): boolean {
  return isLoopbackHost(location.hostname) && location.port === "5173";
}

function isLocalDevBackend(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" && url.port === "8000" && isLoopbackHost(url.hostname);
  } catch {
    return false;
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function hostForUrl(hostname: string): string {
  return hostname.includes(":") && !hostname.startsWith("[") ? `[${hostname}]` : hostname;
}
