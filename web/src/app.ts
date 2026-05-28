import { StudyLensApi } from "./api.js";
import {
  loadSettings,
  parseScopeNotes,
  resolveBackendUrl,
  sanitizeFilename,
} from "./state.js";
import { marked } from "marked";
import type { Tokens } from "marked";
import markedKatex from "marked-katex-extension";
import hljs from "highlight.js/lib/common";

marked.use(markedKatex({ throwOnError: false }));

marked.use({
  renderer: {
    code({ text, lang }: Tokens.Code): string {
      const language = lang && hljs.getLanguage(lang) ? lang : "plaintext";
      const highlighted = hljs.highlight(text, { language }).value;
      return `<pre><code class="hljs language-${language}">${highlighted}</code></pre>\n`;
    },
  },
});

const CALLOUT_TITLES: Record<string, string> = {
  NOTE: "Note", TIP: "Tip", IMPORTANT: "Important", WARNING: "Warning", CAUTION: "Caution",
};

function preprocessMath(text: string): string {
  // marked strips backslashes from \[ and \( before KaTeX gets a chance to see them.
  // Convert to $$ / $ so they survive markdown parsing.
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_m, math: string) => `$$${math}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_m, math: string) => `$${math}$`);
}

function renderAnswer(markdown: string): string {
  const html = marked.parse(preprocessMath(markdown)) as string;
  // Transform GFM callouts: > [!NOTE] / > [!WARNING] etc.
  return html.replace(
    /<blockquote>\s*<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*([\s\S]*?)<\/blockquote>/gi,
    (_match, type: string, inner: string) => {
      const t = type.toUpperCase();
      return `<div class="callout callout-${t.toLowerCase()}"><strong class="callout-label">${CALLOUT_TITLES[t]}</strong><p>${inner}</div>`;
    }
  );
}
import { citationLabel, clippedText, resultTitle, scoreLabel } from "./render.js";
import type { DiscoveredCourse, ResourceKind, SearchResult } from "./types.js";

const elements = {
  // Course library (main page)
  coursesDiscover: byId<HTMLButtonElement>("courses-discover"),
  coursesIndex: byId<HTMLButtonElement>("courses-index"),
  coursesSelectAll: byId<HTMLButtonElement>("courses-select-all"),
  coursesStatus: byId<HTMLSpanElement>("courses-status"),
  coursesSummary: byId<HTMLSpanElement>("courses-summary"),
  coursesList: byId<HTMLUListElement>("courses-list"),
  coursesProgress: byId<HTMLElement>("courses-progress"),
  coursesProgressList: byId<HTMLUListElement>("courses-progress-list"),
  // Sidebar course context
  sidebarCourseContext: byId<HTMLElement>("sidebar-course-context"),
  sidebarCourseCode: byId<HTMLSpanElement>("sidebar-course-code"),
  sidebarCourseTitle: byId<HTMLSpanElement>("sidebar-course-title"),
  sidebarCourseNav: byId<HTMLElement>("sidebar-course-nav"),
  // Course sub-page header
  backToCoursesBtn: byId<HTMLButtonElement>("back-to-courses"),
  coursePageCode: byId<HTMLSpanElement>("course-page-code"),
  coursePageTitle: byId<HTMLSpanElement>("course-page-title"),
  reindexBtn: byId<HTMLButtonElement>("reindex-btn"),
  reindexStatus: byId<HTMLSpanElement>("reindex-status"),
  // Ask tab
  askExercises: byId<HTMLInputElement>("ask-exercises"),
  askTopK: byId<HTMLInputElement>("ask-top-k"),
  askQuestion: byId<HTMLTextAreaElement>("ask-question"),
  askSubmit: byId<HTMLButtonElement>("ask-submit"),
  askStatus: byId<HTMLSpanElement>("ask-status"),
  answerCard: byId<HTMLElement>("answer-card"),
  citationList: byId<HTMLElement>("citation-list"),
  // Generate tab
  modeButtons: Array.from(document.querySelectorAll<HTMLButtonElement>(".segment")),
  scopeNotes: byId<HTMLTextAreaElement>("scope-notes"),
  questionCountField: byId<HTMLElement>("question-count-field"),
  questionCount: byId<HTMLInputElement>("question-count"),
  generateSubmit: byId<HTMLButtonElement>("generate-submit"),
  downloadLatex: byId<HTMLButtonElement>("download-latex"),
  generateStatus: byId<HTMLSpanElement>("generate-status"),
  latexOutput: byId<HTMLPreElement>("latex-output"),
  // Retrieve tab
  retrieveTopK: byId<HTMLInputElement>("retrieve-top-k"),
  retrieveQuery: byId<HTMLTextAreaElement>("retrieve-query"),
  retrieveKinds: byId<HTMLElement>("retrieve-kinds"),
  retrieveSubmit: byId<HTMLButtonElement>("retrieve-submit"),
  retrieveStatus: byId<HTMLSpanElement>("retrieve-status"),
  retrieveResults: byId<HTMLElement>("retrieve-results"),
};

const shell = document.querySelector<HTMLElement>(".shell")!;

let api = new StudyLensApi("http://localhost:8000");
let generationMode: "cheatsheet" | "exam" = "cheatsheet";
let latestLatex = "";
let discoveredCourses: DiscoveredCourse[] = [];
let currentCourse: DiscoveredCourse | null = null;
const selectedCourseCodes = new Set<string>();

init();

function init(): void {
  const settings = loadSettings();
  settings.backendUrl = resolveBackendUrl(settings, window.location);
  api = new StudyLensApi(settings.backendUrl);

  elements.backToCoursesBtn.addEventListener("click", showCoursesPage);
  elements.reindexBtn.addEventListener("click", handleReindex);
  elements.askSubmit.addEventListener("click", handleAsk);
  elements.generateSubmit.addEventListener("click", handleGenerate);
  elements.downloadLatex.addEventListener("click", handleDownloadLatex);
  elements.retrieveSubmit.addEventListener("click", handleRetrieve);
  elements.coursesDiscover.addEventListener("click", handleDiscoverCourses);
  elements.coursesIndex.addEventListener("click", handleIndexSelected);
  elements.coursesSelectAll.addEventListener("click", handleSelectAllCourses);
  elements.modeButtons.forEach((button) => {
    button.addEventListener("click", () =>
      setGenerationMode(button.dataset.mode === "exam" ? "exam" : "cheatsheet")
    );
  });
  document.querySelectorAll<HTMLButtonElement>(".sidebar-nav-item[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => activateCourseTab(btn.dataset.tab ?? "ask"));
  });

  updateCoursesActions();
  void loadCachedCourses();
}

// ── Navigation ────────────────────────────────────────────────────────

function showCoursesPage(): void {
  shell.classList.add("mode-courses");
  byId("view-courses").classList.add("active");
  byId("view-course").classList.remove("active");
  elements.sidebarCourseContext.classList.add("hidden");
  elements.sidebarCourseNav.classList.add("hidden");
  currentCourse = null;
}

function enterCourse(course: DiscoveredCourse): void {
  currentCourse = course;
  elements.coursePageCode.textContent = course.code;
  elements.coursePageTitle.textContent = stripCodePrefix(course.title);
  elements.reindexStatus.textContent = course.indexed_at
    ? `Processed ${formatTimestamp(course.indexed_at)}`
    : "";
  elements.sidebarCourseCode.textContent = course.code;
  elements.sidebarCourseTitle.textContent = stripCodePrefix(course.title);
  elements.sidebarCourseContext.classList.remove("hidden");
  elements.sidebarCourseNav.classList.remove("hidden");
  shell.classList.remove("mode-courses");
  byId("view-courses").classList.remove("active");
  byId("view-course").classList.add("active");
  activateCourseTab("ask");
  // Clear stale results from previous session
  elements.answerCard.textContent = "";
  elements.answerCard.classList.add("hidden");
  elements.citationList.replaceChildren();
  elements.retrieveResults.replaceChildren();
}

function activateCourseTab(tab: string): void {
  document.querySelectorAll<HTMLButtonElement>(".sidebar-nav-item[data-tab]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll<HTMLElement>(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.id === `tab-${tab}`);
  });
}

// ── Course library ────────────────────────────────────────────────────

async function loadCachedCourses(): Promise<void> {
  try {
    const { courses } = await api.listCourses();
    if (courses.length === 0) return;
    discoveredCourses = courses;
    selectedCourseCodes.clear();
    renderCourseList();
    updateCoursesSummary();
    updateCoursesActions();
    const latest = courses.reduce<string | null>(
      (acc, c) => (c.updated_at && (!acc || c.updated_at > acc) ? c.updated_at : acc),
      null
    );
    setStatus(
      elements.coursesStatus,
      latest
        ? `Loaded ${courses.length} courses · last refreshed ${formatTimestamp(latest)}`
        : `Loaded ${courses.length} courses`
    );
  } catch {
    // Backend offline at startup — leave panel empty.
  }
}

async function handleDiscoverCourses(): Promise<void> {
  await withBusy(elements.coursesDiscover, elements.coursesStatus, "Discovering", async () => {
    const response = await api.discoverCourses();
    discoveredCourses = response.courses;
    selectedCourseCodes.clear();
    renderCourseList();
    elements.coursesProgressList.replaceChildren();
    elements.coursesProgress.hidden = true;
    if (response.error) {
      setStatus(elements.coursesStatus, response.error, "error");
    } else {
      setStatus(elements.coursesStatus, "");
    }
    updateCoursesSummary(response.dropped_titles.length);
    updateCoursesActions();
  });
}

function renderCourseList(): void {
  elements.coursesList.replaceChildren(
    ...discoveredCourses.map((course) => createCourseCard(course))
  );
}

function createCourseCard(course: DiscoveredCourse): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "course-card";
  if (selectedCourseCodes.has(course.code)) li.classList.add("selected");

  const label = document.createElement("label");
  label.htmlFor = `course-${course.code}`;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = `course-${course.code}`;
  checkbox.value = course.code;
  checkbox.checked = selectedCourseCodes.has(course.code);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) {
      selectedCourseCodes.add(course.code);
    } else {
      selectedCourseCodes.delete(course.code);
    }
    li.classList.toggle("selected", checkbox.checked);
    updateCoursesActions();
  });

  const code = document.createElement("span");
  code.className = "course-code";
  code.textContent = course.code;

  const info = document.createElement("span");
  info.className = "course-info";

  const title = document.createElement("span");
  title.className = "course-title";
  title.textContent = stripCodePrefix(course.title);
  title.title = course.title;

  const meta = document.createElement("span");
  meta.className = "course-url";
  if (course.indexed_at) {
    meta.textContent = `Processed ${formatTimestamp(course.indexed_at)}`;
  } else if (course.edstem_url) {
    meta.textContent = shortUrl(course.edstem_url);
    meta.title = course.edstem_url;
  }

  info.append(title, meta);
  label.append(checkbox, code, info);
  li.append(label);

  if (course.indexed_at) {
    const enterBtn = document.createElement("button");
    enterBtn.type = "button";
    enterBtn.className = "enter-course-btn";
    enterBtn.textContent = "Enter →";
    enterBtn.addEventListener("click", () => enterCourse(course));
    li.append(enterBtn);
  }

  return li;
}

function updateCoursesSummary(dropped: number = 0): void {
  if (discoveredCourses.length === 0) {
    elements.coursesSummary.textContent = "";
    return;
  }
  const dropNote = dropped > 0 ? ` · ${dropped} without a code skipped` : "";
  elements.coursesSummary.textContent = `${discoveredCourses.length} courses${dropNote}`;
}

function updateCoursesActions(): void {
  const selectedCount = selectedCourseCodes.size;
  elements.coursesIndex.disabled = selectedCount === 0;
  elements.coursesIndex.textContent =
    selectedCount > 0 ? `Process ${selectedCount} selected` : "Process selected";
  elements.coursesSelectAll.hidden = discoveredCourses.length === 0;
  elements.coursesSelectAll.textContent =
    selectedCount === discoveredCourses.length && discoveredCourses.length > 0
      ? "Clear selection"
      : "Select all";
}

function handleSelectAllCourses(): void {
  const allSelected = selectedCourseCodes.size === discoveredCourses.length;
  selectedCourseCodes.clear();
  if (!allSelected) {
    for (const course of discoveredCourses) selectedCourseCodes.add(course.code);
  }
  renderCourseList();
  updateCoursesActions();
}

async function confirmIndexedCourse(code: string): Promise<DiscoveredCourse | null> {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (attempt > 0) await delay(5000);
    try {
      const { courses } = await api.listCourses();
      const indexedCourse = courses.find((course) => course.code === code && course.indexed_at);
      if (indexedCourse) {
        const idx = discoveredCourses.findIndex((course) => course.code === code);
        if (idx >= 0) discoveredCourses[idx] = indexedCourse;
        if (currentCourse?.code === code) currentCourse = indexedCourse;
        renderCourseList();
        updateCoursesSummary();
        updateCoursesActions();
        return indexedCourse;
      }
    } catch {
      // Keep the original indexing error if the status check also fails.
    }
  }
  return null;
}

// Cap simultaneous indexing jobs to keep RAM and rate limits in check.
const INDEX_CONCURRENCY = 3;
const CHEATSHEET_CONTEXT_TOP_K = 40;
const PREDICTED_EXAM_CONTEXT_TOP_K = 50;

async function handleIndexSelected(): Promise<void> {
  const targets = discoveredCourses.filter((c) => selectedCourseCodes.has(c.code));
  if (targets.length === 0) {
    setStatus(elements.coursesStatus, "Tick at least one course first", "error");
    return;
  }
  elements.coursesIndex.disabled = true;
  elements.coursesDiscover.disabled = true;
  elements.coursesSelectAll.disabled = true;
  elements.coursesProgress.hidden = false;
  elements.coursesProgressList.replaceChildren(
    ...targets.map((c) => createProgressRow(c.code, c.title, "queued"))
  );

  const queue = [...targets];
  let completed = 0;

  async function worker(): Promise<void> {
    while (true) {
      const course = queue.shift();
      if (!course) return;
      const row = elements.coursesProgressList.querySelector<HTMLElement>(
        `[data-code="${course.code}"]`
      );
      if (!row) continue;
      setProgressStatus(row, "running", "Processing…");
      try {
        const report = await api.autoIndexCourse({
          course_id: course.code,
          course_title: course.title,
        });
        setProgressStatus(
          row,
          "done",
          `${report.indexed_resources}/${report.discovered_resources} resources · ${report.indexed_chunks} chunks`
        );
        // Update local indexed_at so the Enter button appears without a reload.
        const idx = discoveredCourses.findIndex((c) => c.code === course.code);
        if (idx >= 0) {
          discoveredCourses[idx] = { ...discoveredCourses[idx], indexed_at: new Date().toISOString() };
        }
      } catch (error) {
        setProgressStatus(row, "running", "Checking status…");
        const indexedCourse = await confirmIndexedCourse(course.code);
        if (indexedCourse?.indexed_at) {
          setProgressStatus(
            row,
            "done",
            `Processed ${formatTimestamp(indexedCourse.indexed_at)} · response lost`
          );
        } else {
          setProgressStatus(row, "failed", error instanceof Error ? error.message : "failed");
        }
      }
      completed += 1;
      setStatus(elements.coursesStatus, `${completed}/${targets.length} done`);
    }
  }

  const workers = Array.from(
    { length: Math.min(INDEX_CONCURRENCY, targets.length) },
    () => worker()
  );
  try {
    await Promise.all(workers);
    setStatus(elements.coursesStatus, `Done · ${completed}/${targets.length}`);
    // Re-render so newly indexed courses get their Enter button.
    renderCourseList();
  } finally {
    elements.coursesIndex.disabled = selectedCourseCodes.size === 0;
    elements.coursesDiscover.disabled = false;
    elements.coursesSelectAll.disabled = false;
  }
}

function createProgressRow(code: string, title: string, status: ProgressStatus): HTMLLIElement {
  const li = document.createElement("li");
  li.className = "course-progress-row";
  li.dataset.code = code;

  const codeNode = document.createElement("span");
  codeNode.className = "course-code";
  codeNode.textContent = code;

  const body = document.createElement("div");
  body.className = "progress-body";

  const titleNode = document.createElement("span");
  titleNode.className = "progress-title";
  titleNode.textContent = stripCodePrefix(title);
  titleNode.title = title;

  const summary = document.createElement("span");
  summary.className = "progress-summary";
  summary.dataset.role = "summary";
  summary.textContent = "Queued";

  body.append(titleNode, summary);

  const statusBadge = document.createElement("span");
  statusBadge.className = `progress-status ${status}`;
  statusBadge.dataset.role = "status";
  statusBadge.textContent = status;

  li.append(codeNode, body, statusBadge);
  return li;
}

type ProgressStatus = "queued" | "running" | "done" | "failed";

function setProgressStatus(row: HTMLElement, status: ProgressStatus, summary: string): void {
  const statusNode = row.querySelector<HTMLElement>('[data-role="status"]');
  if (statusNode) {
    statusNode.className = `progress-status ${status}`;
    statusNode.textContent = status;
  }
  const summaryNode = row.querySelector<HTMLElement>('[data-role="summary"]');
  if (summaryNode) summaryNode.textContent = summary;
}

// ── Re-index from course sub-page ─────────────────────────────────────

async function handleReindex(): Promise<void> {
  if (!currentCourse) return;
  const course = currentCourse;
  elements.reindexBtn.disabled = true;
  elements.reindexStatus.textContent = "Processing…";
  elements.reindexStatus.style.color = "var(--muted)";
  try {
    const report = await api.autoIndexCourse({
      course_id: course.code,
      course_title: course.title,
    });
    const ts = new Date().toISOString();
    // Update local state
    const idx = discoveredCourses.findIndex((c) => c.code === course.code);
    if (idx >= 0) discoveredCourses[idx] = { ...discoveredCourses[idx], indexed_at: ts };
    currentCourse = { ...course, indexed_at: ts };
    elements.reindexStatus.textContent =
      `Processed ${report.indexed_resources}/${report.discovered_resources} resources · ${report.indexed_chunks} chunks · ${formatTimestamp(ts)}`;
    elements.reindexStatus.style.color = "var(--muted)";
  } catch (error) {
    elements.reindexStatus.textContent = "Checking status…";
    const indexedCourse = await confirmIndexedCourse(course.code);
    if (indexedCourse?.indexed_at) {
      currentCourse = indexedCourse;
      elements.reindexStatus.textContent =
        `Processed ${formatTimestamp(indexedCourse.indexed_at)} · response lost`;
      elements.reindexStatus.style.color = "var(--muted)";
    } else {
      elements.reindexStatus.textContent = error instanceof Error ? error.message : "Failed";
      elements.reindexStatus.style.color = "var(--danger)";
    }
  } finally {
    elements.reindexBtn.disabled = false;
  }
}

// ── Ask ───────────────────────────────────────────────────────────────

async function handleAsk(): Promise<void> {
  if (!currentCourse) return;
  const question = elements.askQuestion.value.trim();
  if (!question) {
    setStatus(elements.askStatus, "Question required", "error");
    return;
  }
  await withBusy(elements.askSubmit, elements.askStatus, "Asking", async () => {
    const answer = await api.ask({
      question,
      course_id: currentCourse!.code,
      top_k: numeric(elements.askTopK.value, 5),
      include_exercises: elements.askExercises.checked,
    });
    elements.answerCard.innerHTML = renderAnswer(answer.answer);
    elements.answerCard.classList.remove("hidden");
    elements.citationList.replaceChildren(
      ...answer.citations.map((citation, index) =>
        resultNode(citationLabel(citation, index), citation.quote || "", citation.source_url || "")
      )
    );
    setStatus(elements.askStatus, "Done");
  });
}

// ── Generate ──────────────────────────────────────────────────────────

async function handleGenerate(): Promise<void> {
  if (!currentCourse) return;
  await withBusy(elements.generateSubmit, elements.generateStatus, "Generating", async () => {
    const base = {
      course_id: currentCourse!.code,
      course_title: currentCourse!.title,
      scope_notes: parseScopeNotes(elements.scopeNotes.value),
      top_k:
        generationMode === "cheatsheet"
          ? CHEATSHEET_CONTEXT_TOP_K
          : PREDICTED_EXAM_CONTEXT_TOP_K,
    };
    const response =
      generationMode === "cheatsheet"
        ? await api.generateCheatsheet(base)
        : await api.generatePredictedExam({
            ...base,
            question_count: numeric(elements.questionCount.value, 4),
          });
    latestLatex = response.latex;
    elements.latexOutput.textContent = latestLatex;
    elements.downloadLatex.disabled = false;
    setStatus(elements.generateStatus, "Done");
  });
}

function handleDownloadLatex(): void {
  if (!latestLatex || !currentCourse) return;
  const name = `${sanitizeFilename(`${currentCourse.code}-${generationMode}`)}.tex`;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([latestLatex], { type: "application/x-tex" }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

function setGenerationMode(mode: "cheatsheet" | "exam"): void {
  generationMode = mode;
  elements.modeButtons.forEach((button) =>
    button.classList.toggle("active", button.dataset.mode === mode)
  );
  elements.questionCountField.classList.toggle("hidden", mode !== "exam");
}

// ── Retrieve ──────────────────────────────────────────────────────────

async function handleRetrieve(): Promise<void> {
  if (!currentCourse) return;
  const query = elements.retrieveQuery.value.trim();
  if (!query) {
    setStatus(elements.retrieveStatus, "Query required", "error");
    return;
  }
  await withBusy(elements.retrieveSubmit, elements.retrieveStatus, "Retrieving", async () => {
    const response = await api.retrieve({
      query,
      course_id: currentCourse!.code,
      kinds: selectedKinds(),
      top_k: numeric(elements.retrieveTopK.value, 8),
    });
    renderResults(response.results);
    setStatus(elements.retrieveStatus, `${response.results.length} results`);
  });
}

// ── Render helpers ────────────────────────────────────────────────────

function renderResults(results: SearchResult[]): void {
  elements.retrieveResults.replaceChildren(
    ...results.map((result) =>
      resultNode(
        resultTitle(result),
        clippedText(result.chunk.text),
        `${result.chunk.kind} · ${scoreLabel(result.score)} · chunk ${result.chunk.position}`
      )
    )
  );
}

function resultNode(title: string, text: string, meta: string): HTMLElement {
  const article = document.createElement("article");
  article.className = "result-item";

  const header = document.createElement("div");
  header.className = "result-meta";

  const titleNode = document.createElement("span");
  titleNode.className = "result-title";
  titleNode.textContent = title;

  const metaNode = document.createElement("span");
  metaNode.textContent = meta;

  const body = document.createElement("p");
  body.className = "result-text";
  body.textContent = text;

  header.append(titleNode, metaNode);
  article.append(header, body);
  return article;
}

// ── Utilities ─────────────────────────────────────────────────────────

function stripCodePrefix(title: string): string {
  // EdStem titles like "COMP 50001: Algorithm Design..." — drop leading code.
  const stripped = title
    .replace(/^\s*[A-Z]{2,5}\s*[-\s]?\s*\d{3,5}(?:[./][A-Za-z0-9]+)?\s*[:\-—]\s*/, "")
    .trim();
  return stripped || title;
}

function shortUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return parsed.host.replace(/^www\./, "") + parsed.pathname;
  } catch {
    return url;
  }
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function withBusy(
  button: HTMLButtonElement,
  status: HTMLElement,
  label: string,
  action: () => Promise<void>
): Promise<void> {
  const original = button.textContent || "";
  button.disabled = true;
  button.textContent = label;
  setStatus(status, label);
  try {
    await action();
  } catch (error) {
    setStatus(status, error instanceof Error ? error.message : "Request failed", "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function selectedKinds(): ResourceKind[] {
  return Array.from(
    elements.retrieveKinds.querySelectorAll<HTMLInputElement>("input:checked")
  ).map((input) => input.value as ResourceKind);
}

function numeric(value: string, fallback: number): number {
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function setStatus(node: HTMLElement, value: string, mode: "ok" | "error" = "ok"): void {
  node.textContent = value;
  node.style.color = mode === "error" ? "var(--danger)" : "var(--muted)";
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`Missing #${id}`);
  return element as T;
}
