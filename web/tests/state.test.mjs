import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SETTINGS,
  loadSettings,
  parseScopeNotes,
  resolveBackendUrl,
  sanitizeFilename,
  saveSettings
} from "../dist/state.js";
import { autoIndexItemMeta, citationLabel, clippedText, scoreLabel } from "../dist/render.js";

class MemoryStorage {
  constructor() {
    this.values = new Map();
  }
  getItem(key) {
    return this.values.get(key) ?? null;
  }
  setItem(key, value) {
    this.values.set(key, String(value));
  }
}

test("settings round trip through storage", () => {
  const storage = new MemoryStorage();
  saveSettings({ backendUrl: "http://localhost:8000" }, storage);
  assert.deepEqual(loadSettings(storage), { backendUrl: "http://localhost:8000" });
});

test("loadSettings tolerates invalid storage", () => {
  const storage = new MemoryStorage();
  storage.setItem("studylens.web.settings", "{");

  assert.deepEqual(loadSettings(storage), DEFAULT_SETTINGS);
});

test("default backend points at local API", () => {
  assert.equal(DEFAULT_SETTINGS.backendUrl, "http://localhost:8000");
});

test("default backend can be injected at build runtime", async () => {
  globalThis.STUDYLENS_BACKEND_URL = "https://api.example.com";
  const state = await import(`../dist/state.js?configured=${Date.now()}`);
  delete globalThis.STUDYLENS_BACKEND_URL;

  assert.equal(state.DEFAULT_SETTINGS.backendUrl, "https://api.example.com");
});

test("scope notes and filenames are normalized", () => {
  assert.deepEqual(parseScopeNotes("Week 1\n\n No flow "), ["Week 1", "No flow"]);
  assert.equal(sanitizeFilename("COMP70001 Cheatsheet.tex"), "comp70001-cheatsheet-tex");
});

test("resolveBackendUrl uses current origin for API-served app", () => {
  assert.equal(
    resolveBackendUrl(DEFAULT_SETTINGS, {
      origin: "http://127.0.0.1:8000",
      pathname: "/app",
      port: "8000"
    }),
    "http://127.0.0.1:8000",
  );
  assert.equal(
    resolveBackendUrl(DEFAULT_SETTINGS, {
      origin: "https://study-api.up.railway.app",
      pathname: "/app",
      port: ""
    }),
    "https://study-api.up.railway.app",
  );
  assert.equal(
    resolveBackendUrl({ ...DEFAULT_SETTINGS, backendUrl: "http://api.local" }, {
      origin: "http://127.0.0.1:8000",
      pathname: "/app",
      port: "8000"
    }),
    "http://api.local",
  );
});

test("render helpers produce compact labels", () => {
  // No page/timestamp → only title shown (chunk position no longer displayed)
  assert.equal(
    citationLabel({ course_id: "C", resource_id: "r", title: "Lecture", position: 2 }, 0),
    "1. Lecture",
  );
  // PDF page number
  assert.equal(
    citationLabel({ course_id: "C", resource_id: "r", title: "Lecture", page: 5 }, 0),
    "1. Lecture · p.5",
  );
  // Video timestamp
  assert.equal(
    citationLabel({ course_id: "C", resource_id: "r", title: "Lecture", start_seconds: 154 }, 0),
    "1. Lecture · 2:34",
  );
  assert.equal(scoreLabel(0.873), "87%");
  assert.equal(clippedText("abc", 10), "abc");
  assert.equal(clippedText("abcdef", 5), "abcd…");
  assert.equal(
    autoIndexItemMeta({
      title: "Notes",
      kind: "material",
      stage: "scientia",
      status: "indexed",
      chunks: 2
    }),
    "scientia · material · 2 chunks"
  );
  assert.equal(autoIndexItemMeta({ title: "Slides", kind: "material", status: "skipped", chunks: 0 }), "material · skipped");
});
