import assert from "node:assert/strict";
import test from "node:test";

import { StudyLensApi, normalizeBaseUrl } from "../dist/api.js";

test("normalizeBaseUrl cleans user input", () => {
  assert.equal(normalizeBaseUrl(" http://localhost:8000/// "), "http://localhost:8000");
  assert.equal(normalizeBaseUrl(""), "http://localhost:8000");
});

test("StudyLensApi posts index, ask, retrieve, and generation payloads", async () => {
  const calls = [];
  const fetchImpl = async (input, init = {}) => {
    calls.push({ input, init });
    const path = String(input).replace("http://localhost:8000", "");
    const bodies = {
      "/chunks": { indexed_chunks: 2 },
      "/auth/login": {
        user: { id: 1, username: "alice", grade: "Year 3", course: "Computing" },
        created: true,
        browser_state_ready: false,
        needs_browser_state: true
      },
      "/auth/session": {
        user: { id: 1, username: "alice", grade: "Year 3", course: "Computing" },
        created: false,
        browser_state_ready: true,
        needs_browser_state: false
      },
      "/auth/logout": { status: "ok" },
      "/browser-state/start": { running: true, completed: false, ready: false, total_steps: 1 },
      "/browser-state/advance": { running: false, completed: true, ready: true, total_steps: 1 },
      "/browser-state/status": { running: false, completed: true, ready: true, total_steps: 0 },
      "/browser-state/cancel": { running: false, completed: false, ready: false, total_steps: 0 },
      "/index/course": {
        course_id: "COMP70001",
        course_title: "Advanced Algorithms",
        discovered_resources: 1,
        indexed_resources: 1,
        indexed_chunks: 2,
        items: []
      },
      "/ask": { question: "q", answer: "a", citations: [], follow_up: null },
      "/retrieve": { results: [] },
      "/generate/cheatsheet": { latex: "\\documentclass{article}" },
      "/generate/predicted-exam": { latex: "\\documentclass{article}" },
    };
    return new Response(JSON.stringify(bodies[path]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  const api = new StudyLensApi("http://localhost:8000", fetchImpl);

  assert.equal((await api.login({
    username: "alice",
    grade: "Year 3",
    course: "Computing",
    password: "correct horse battery staple",
  })).needs_browser_state, true);
  assert.equal((await api.session()).browser_state_ready, true);
  assert.equal((await api.logout()).status, "ok");
  assert.equal((await api.startBrowserState()).running, true);
  assert.equal((await api.advanceBrowserState()).ready, true);
  assert.equal((await api.browserStateStatus()).completed, true);
  assert.equal((await api.cancelBrowserState()).ready, false);
  assert.equal((await api.indexText({
    course_id: "COMP70001",
    title: "Notes",
    text: "Memoization",
    kind: "material",
  })).indexed_chunks, 2);
  assert.equal((await api.autoIndexCourse({
    course_id: "COMP70001",
    course_title: "Advanced Algorithms",
    course_url: null
  })).indexed_chunks, 2);
  assert.equal((await api.ask({
    question: "q",
    course_id: "COMP70001",
    top_k: 5,
    include_exercises: true,
  })).answer, "a");
  assert.deepEqual((await api.retrieve({
    query: "q",
    course_id: "COMP70001",
    kinds: ["material"],
    top_k: 3,
  })).results, []);
  assert.match((await api.generateCheatsheet({
    course_id: "COMP70001",
    course_title: "Advanced Algorithms",
    scope_notes: [],
    top_k: 40,
  })).latex, /documentclass/);
  assert.match((await api.generatePredictedExam({
    course_id: "COMP70001",
    course_title: "Advanced Algorithms",
    scope_notes: [],
    top_k: 40,
    question_count: 4,
  })).latex, /documentclass/);

  assert.equal(calls.length, 13);
  assert.equal(JSON.parse(calls[0].init.body).username, "alice");
  assert.equal(calls.every((call) => call.init.credentials === "include"), true);
  assert.equal(JSON.parse(calls[7].init.body).title, "Notes");
  assert.equal(JSON.parse(calls[8].init.body).course_title, "Advanced Algorithms");
  assert.equal(JSON.parse(calls[9].init.body).include_exercises, true);
});

test("StudyLensApi surfaces backend errors", async () => {
  const api = new StudyLensApi("http://localhost:8000", async () => {
    return new Response("bad request", { status: 400 });
  });

  await assert.rejects(
    () => api.health(),
    /StudyLens API 400: bad request/
  );
});
