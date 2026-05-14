import assert from "node:assert/strict";
import test from "node:test";

import { collectPageContext, extractCourseId } from "../dist/pageContext.js";

test("extractCourseId finds Imperial-style course identifiers", () => {
  assert.equal(extractCourseId("Welcome to COMP70001 Advanced Algorithms"), "COMP70001");
  assert.equal(extractCourseId("no identifier here"), null);
});

test("collectPageContext trims visible and selected text", () => {
  const fakeDocument = {
    title: "COMP70001 Lecture",
    location: { href: "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001" },
    body: { innerText: "A".repeat(6000) },
    getSelection: () => ({ toString: () => "selected paragraph" })
  };

  const context = collectPageContext(fakeDocument);

  assert.equal(context.inferredCourseId, "COMP70001");
  assert.equal(context.selectedText, "selected paragraph");
  assert.equal(context.visibleText.length, 5000);
});

