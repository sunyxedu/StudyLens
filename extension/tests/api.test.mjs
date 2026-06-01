import assert from "node:assert/strict";
import test from "node:test";

import { StudyLensClient, normalizeBaseUrl } from "../dist/api.js";

test("normalizeBaseUrl trims trailing slashes and defaults localhost", () => {
  assert.equal(normalizeBaseUrl(" http://localhost:8000/// "), "http://localhost:8000");
  assert.equal(normalizeBaseUrl(""), "http://localhost:8000");
});

test("StudyLensClient.ask posts expected payload", async () => {
  const calls = [];
  const fetchImpl = async (input, init) => {
    calls.push({ input, init });
    return new Response(
      JSON.stringify({
        question: "What is DP?",
        answer: "Memoization stores solved subproblems.",
        citations: [],
        follow_up: null
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  };
  const client = new StudyLensClient("http://localhost:8000/", fetchImpl);

  const answer = await client.ask({ question: "What is DP?", course_id: "COMP70001" });

  assert.equal(answer.answer, "Memoization stores solved subproblems.");
  assert.equal(calls[0].input, "http://localhost:8000/ask");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(JSON.parse(calls[0].init.body).course_id, "COMP70001");
  assert.equal(JSON.parse(calls[0].init.body).include_exercises, false);
});

test("StudyLensClient.ask forwards kinds when provided", async () => {
  const calls = [];
  const fetchImpl = async (input, init) => {
    calls.push({ input, init });
    return new Response(
      JSON.stringify({
        question: "What did the lecturer cover about DP?",
        answer: "Memoization and tabulation.",
        citations: [],
        follow_up: null
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  };
  const client = new StudyLensClient("http://localhost:8000", fetchImpl);

  await client.ask({
    question: "What did the lecturer cover about DP?",
    course_id: "COMP70001",
    kinds: ["transcript"]
  });

  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(body.kinds, ["transcript"]);
  assert.equal(body.include_exercises, false);
});

test("StudyLensClient.ask raises useful API errors", async () => {
  const client = new StudyLensClient("http://localhost:8000", async () => {
    return new Response("broken", { status: 500, statusText: "Server Error" });
  });

  await assert.rejects(
    () => client.ask({ question: "x" }),
    /StudyLens API error 500: broken/
  );
});
