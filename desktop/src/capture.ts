import { BrowserWindow, dialog } from "electron";

type BrowserStateStep = {
  key: string;
  title: string;
  url: string;
  instruction: string;
};

const BROWSER_STATE_STEPS: BrowserStateStep[] = [
  {
    key: "scientia",
    title: "Scientia",
    url: "https://scientia.doc.ic.ac.uk/2526/timeline",
    instruction: "Log into Imperial SSO and wait for the Scientia timeline to load.",
  },
  {
    key: "panopto",
    title: "Panopto",
    url: "https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx#isSharedWithMe=true",
    instruction: "Complete the Panopto sign-in flow and wait for the session list.",
  },
  {
    key: "exams",
    title: "DOC Exams",
    url: "https://exams.doc.ic.ac.uk/",
    instruction: "Log into the Department of Computing exams site and wait for the past papers index.",
  },
  {
    key: "edstem",
    title: "EdStem",
    url: "https://edstem.org/us/dashboard",
    instruction: "Log into EdStem and wait for the dashboard to load.",
  },
];

let setupInProgress = false;

export async function captureAndUploadBrowserState(window: BrowserWindow): Promise<void> {
  if (setupInProgress) {
    throw new Error("Browser setup is already running.");
  }

  setupInProgress = true;
  try {
    const state = await captureBrowserState(window);
    await uploadBrowserState(window, state);
  } finally {
    setupInProgress = false;
  }
}

async function captureBrowserState(window: BrowserWindow): Promise<unknown> {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({
    headless: false,
    args: ["--disable-blink-features=AutomationControlled"],
  });

  try {
    const context = await browser.newContext({
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) " +
        "AppleWebKit/537.36 (KHTML, like Gecko) " +
        "Chrome/136.0.0.0 Safari/537.36",
    });
    const page = await context.newPage();

    for (let index = 0; index < BROWSER_STATE_STEPS.length; index += 1) {
      const step = BROWSER_STATE_STEPS[index];
      await page.goto(step.url, { waitUntil: "domcontentloaded" });
      const choice = await dialog.showMessageBox(window, {
        type: "info",
        buttons: ["Continue", "Cancel"],
        defaultId: 0,
        cancelId: 1,
        title: `StudyLens setup: ${step.title}`,
        message: `${step.title} (${index + 1}/${BROWSER_STATE_STEPS.length})`,
        detail: `${step.instruction}\n\nWhen the page is loaded and you are signed in, click Continue.`,
      });
      if (choice.response === 1) {
        throw new Error("Browser setup was cancelled.");
      }
    }

    const state = await context.storageState();
    if (!hasAuthMaterial(state)) {
      throw new Error("No browser login state was captured. Finish signing in before continuing.");
    }
    return state;
  } finally {
    await browser.close();
  }
}

async function uploadBrowserState(window: BrowserWindow, state: unknown): Promise<void> {
  const appUrl = window.webContents.getURL();
  const origin = originFor(appUrl);
  const cookieHeader = await cookieHeaderFor(window, origin);
  if (!cookieHeader) {
    throw new Error("Sign into StudyLens in the desktop app before connecting academic sites.");
  }

  const response = await fetch(`${origin}/browser-state/upload`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Cookie: cookieHeader,
    },
    body: JSON.stringify(state),
  });

  if (!response.ok) {
    const body = await response.text().catch(() => response.statusText);
    throw new Error(`Upload failed (${response.status}): ${body}`);
  }
}

async function cookieHeaderFor(window: BrowserWindow, origin: string): Promise<string> {
  const cookies = await window.webContents.session.cookies.get({ url: origin });
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

function originFor(value: string): string {
  try {
    return new URL(value).origin;
  } catch {
    throw new Error("Cannot determine the StudyLens backend URL.");
  }
}

function hasAuthMaterial(state: unknown): boolean {
  if (!state || typeof state !== "object") {
    return false;
  }
  const value = state as { cookies?: unknown; origins?: unknown };
  return (
    (Array.isArray(value.cookies) && value.cookies.length > 0) ||
    (Array.isArray(value.origins) && value.origins.length > 0)
  );
}
