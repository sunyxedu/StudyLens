import { StudyLensApi, normalizeBaseUrl } from "./api.js";
import {
  loadSettings,
  parseScopeNotes,
  resolveBackendUrl,
  sanitizeFilename,
  saveSettings,
} from "./state.js";
import { autoIndexItemMeta, citationLabel, clippedText, resultTitle, scoreLabel } from "./render.js";
import type { AutoIndexReport, DiscoveredCourse, ResourceKind, SearchResult } from "./types.js";

const elements = {
  backendUrl: byId<HTMLInputElement>("backend-url"),
  saveBackend: byId<HTMLButtonElement>("save-backend"),
  healthPill: byId<HTMLSpanElement>("health-pill"),
  navItems: Array.from(document.querySelectorAll<HTMLButtonElement>(".nav-item")),
  views: Array.from(document.querySelectorAll<HTMLElement>(".view")),
  askCourse: byId<HTMLSelectElement>("ask-course"),
  askExercises: byId<HTMLInputElement>("ask-exercises"),
  askTopK: byId<HTMLInputElement>("ask-top-k"),
  askQuestion: byId<HTMLTextAreaElement>("ask-question"),
  askSubmit: byId<HTMLButtonElement>("ask-submit"),
  askStatus: byId<HTMLSpanElement>("ask-status"),
  answerCard: byId<HTMLElement>("answer-card"),
  citationList: byId<HTMLElement>("citation-list"),
  indexCourse: byId<HTMLSelectElement>("index-course"),
  indexCourseStatus: byId<HTMLSpanElement>("index-course-status"),
  indexSubmit: byId<HTMLButtonElement>("index-submit"),
  indexStatus: byId<HTMLSpanElement>("index-status"),
  indexResults: byId<HTMLElement>("index-results"),
  coursesDiscover: byId<HTMLButtonElement>("courses-discover"),
  coursesIndex: byId<HTMLButtonElement>("courses-index"),
  coursesSelectAll: byId<HTMLButtonElement>("courses-select-all"),
  coursesStatus: byId<HTMLSpanElement>("courses-status"),
  coursesSummary: byId<HTMLSpanElement>("courses-summary"),
  coursesList: byId<HTMLUListElement>("courses-list"),
  coursesProgress: byId<HTMLElement>("courses-progress"),
  coursesProgressList: byId<HTMLUListElement>("courses-progress-list"),
  retrieveCourse: byId<HTMLSelectElement>("retrieve-course"),
  retrieveTopK: byId<HTMLInputElement>("retrieve-top-k"),
  retrieveQuery: byId<HTMLTextAreaElement>("retrieve-query"),
  retrieveKinds: byId<HTMLElement>("retrieve-kinds"),
  retrieveSubmit: byId<HTMLButtonElement>("retrieve-submit"),
  retrieveStatus: byId<HTMLSpanElement>("retrieve-status"),
  retrieveResults: byId<HTMLElement>("retrieve-results"),
  generateCourse: byId<HTMLSelectElement>("generate-course"),
  modeButtons: Array.from(document.querySelectorAll<HTMLButtonElement>(".segment")),
  scopeNotes: byId<HTMLTextAreaElement>("scope-notes"),
  generateTopK: byId<HTMLInputElement>("generate-top-k"),
  questionCountField: byId<HTMLElement>("question-count-field"),
  questionCount: byId<HTMLInputElement>("question-count"),
  generateSubmit: byId<HTMLButtonElement>("generate-submit"),
  downloadLatex: byId<HTMLButtonElement>("download-latex"),
  generateStatus: byId<HTMLSpanElement>("generate-status"),
  latexOutput: byId<HTMLPreElement>("latex-output"),
};

let api = new StudyLensApi("http://localhost:8000");
let generationMode: "cheatsheet" | "exam" = "cheatsheet";
let latestLatex = "";
let discoveredCourses: DiscoveredCourse[] = [];
const selectedCourseCodes = new Set<string>();

init();

function init(): void {
  const settings = loadSettings();
  settings.backendUrl = resolveBackendUrl(settings, window.location);
  elements.backendUrl.value = settings.backendUrl;
  api = new StudyLensApi(settings.backendUrl);

  elements.navItems.forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view || "ask"));
  });
  elements.modeButtons.forEach((button) => {
    button.addEventListener("click", () =>
      setGenerationMode(button.dataset.mode === "exam" ? "exam" : "cheatsheet")
    );
  });
  elements.saveBackend.addEventListener("click", handleSaveSettings);
  elements.indexCourse.addEventListener("change", updateIndexCourseStatus);
  elements.askSubmit.addEventListener("click", handleAsk);
  elements.indexSubmit.addEventListener("click", handleIndex);
  elements.retrieveSubmit.addEventListener("click", handleRetrieve);
  elements.generateSubmit.addEventListener("click", handleGenerate);
  elements.downloadLatex.addEventListener("click", handleDownloadLatex);
  elements.coursesDiscover.addEventListener("click", handleDiscoverCourses);
  elements.coursesIndex.addEventListener("click", handleIndexSelected);
  elements.coursesSelectAll.addEventListener("click", handleSelectAllCourses);
  updateCoursesActions();

  void refreshHealth();
  void loadCachedCourses();
}

async function loadCachedCourses(): Promise<void> {
  try {
    const { courses } = await api.listCourses();
    if (courses.length === 0) {
      setStatus(elements.coursesStatus, "");
      return;
    }
    discoveredCourses = courses;
    selectedCourseCodes.clear();
    renderCourseList();
    populateCourseSelects();
    updateCoursesSummary();
    updateCoursesActions();
    const latest = courses.reduce<string | null>(
      (acc, c) => (c.updated_at && (!acc || c.updated_at > acc) ? c.updated_at : acc),
      null
    );
    setStatus(
      elements.coursesStatus,
      latest
        ? `Loaded ${courses.length} cached courses · last refreshed ${formatTimestamp(latest)}`
        : `Loaded ${courses.length} cached courses`
    );
  } catch {
    // Backend offline or first-time setup — leave the panel empty.
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

function populateCourseSelects(): void {
  populateSelect(elements.askCourse, true);
  populateSelect(elements.indexCourse, false);
  populateSelect(elements.retrieveCourse, true);
  populateSelect(elements.generateCourse, false);
  updateIndexCourseStatus();
}

function populateSelect(select: HTMLSelectElement, allowAll: boolean): void {
  const prev = select.value;
  select.replaceChildren();
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = allowAll ? "All courses" : "— select a course —";
  select.appendChild(blank);
  for (const course of discoveredCourses) {
    const option = document.createElement("option");
    option.value = course.code;
    option.textContent = `${course.code} — ${stripCodePrefix(course.title, course.code)}`;
    select.appendChild(option);
  }
  if (prev) select.value = prev;
}

function updateIndexCourseStatus(): void {
  const code = elements.indexCourse.value;
  const el = elements.indexCourseStatus;
  if (!code) {
    el.textContent = "";
    el.className = "course-status";
    return;
  }
  const course = discoveredCourses.find((c) => c.code === code);
  if (!course) {
    el.textContent = "";
    el.className = "course-status";
    return;
  }
  el.className = "course-status";
  el.textContent = course.indexed_at
    ? `Last indexed: ${formatTimestamp(course.indexed_at)}`
    : "Never indexed";
}

function handleSaveSettings(): void {
  const backendUrl = normalizeBaseUrl(elements.backendUrl.value);
  elements.backendUrl.value = backendUrl;
  api = new StudyLensApi(backendUrl);
  saveSettings({ backendUrl });
  void refreshHealth();
}

async function refreshHealth(): Promise<void> {
  elements.healthPill.textContent = "Checking";
  elements.healthPill.className = "pill neutral";
  try {
    const health = await api.health();
    elements.healthPill.textContent = health.vector_store;
    elements.healthPill.className = "pill ok";
  } catch {
    elements.healthPill.textContent = "Offline";
    elements.healthPill.className = "pill error";
  }
}

async function handleAsk(): Promise<void> {
  const question = elements.askQuestion.value.trim();
  if (!question) {
    setStatus(elements.askStatus, "Question required", "error");
    return;
  }
  await withBusy(elements.askSubmit, elements.askStatus, "Asking", async () => {
    const answer = await api.ask({
      question,
      course_id: elements.askCourse.value || null,
      top_k: numeric(elements.askTopK.value, 5),
      include_exercises: elements.askExercises.checked,
    });
    elements.answerCard.textContent = answer.answer;
    elements.answerCard.classList.remove("hidden");
    elements.citationList.replaceChildren(
      ...answer.citations.map((citation, index) =>
        resultNode(citationLabel(citation, index), citation.quote || "", citation.source_url || "")
      )
    );
    setStatus(elements.askStatus, "Done");
  });
}

type ProgressStatus = "queued" | "running" | "done" | "failed";

async function handleDiscoverCourses(): Promise<void> {
  await withBusy(elements.coursesDiscover, elements.coursesStatus, "Discovering", async () => {
    const response = await api.discoverCourses();
    discoveredCourses = response.courses;
    selectedCourseCodes.clear();
    renderCourseList();
    populateCourseSelects();
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

  const label = document.createElement("label");
  label.htmlFor = `course-${course.code}`;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = `course-${course.code}`;
  checkbox.value = course.code;
  checkbox.checked = selectedCourseCodes.has(course.code);
  if (checkbox.checked) li.classList.add("selected");
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
  title.textContent = stripCodePrefix(course.title, course.code);
  title.title = course.title;

  const meta = document.createElement("span");
  meta.className = "course-url";
  if (course.indexed_at) {
    meta.textContent = `Indexed ${formatTimestamp(course.indexed_at)}`;
  } else if (course.edstem_url) {
    meta.textContent = shortUrl(course.edstem_url);
    meta.title = course.edstem_url;
  }

  info.append(title, meta);
  label.append(checkbox, code, info);
  li.append(label);
  return li;
}

function stripCodePrefix(title: string, _code: string): string {
  // EdStem titles like "COMP 50001: Algorithm Design..." — drop the leading code.
  const stripped = title
    .replace(/^\s*[A-Z]{2,5}\s*[-\s]?\s*\d{3,5}(?:[\.\/][A-Za-z0-9]+)?\s*[:\-—]\s*/, "")
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
    selectedCount > 0 ? `Index ${selectedCount} selected` : "Index selected";
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
    for (const course of discoveredCourses) {
      selectedCourseCodes.add(course.code);
    }
  }
  renderCourseList();
  updateCoursesActions();
}

// Cap simultaneous indexing jobs to keep RAM and rate limits in check.
const INDEX_CONCURRENCY = 3;

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
      setProgressStatus(row, "running", "Indexing…");
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
      } catch (error) {
        setProgressStatus(row, "failed", error instanceof Error ? error.message : "failed");
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
  titleNode.textContent = stripCodePrefix(title, code);
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

function setProgressStatus(row: HTMLElement, status: ProgressStatus, summary: string): void {
  const statusNode = row.querySelector<HTMLElement>('[data-role="status"]');
  if (statusNode) {
    statusNode.className = `progress-status ${status}`;
    statusNode.textContent = status;
  }
  const summaryNode = row.querySelector<HTMLElement>('[data-role="summary"]');
  if (summaryNode) summaryNode.textContent = summary;
}

async function handleIndex(): Promise<void> {
  const code = elements.indexCourse.value;
  if (!code) {
    setStatus(elements.indexStatus, "Select a course first", "error");
    return;
  }
  const course = discoveredCourses.find((c) => c.code === code);
  if (!course) {
    setStatus(elements.indexStatus, "Course not found", "error");
    return;
  }
  await withBusy(elements.indexSubmit, elements.indexStatus, "Syncing", async () => {
    const report = await api.autoIndexCourse({
      course_id: course.code,
      course_title: course.title,
    });
    renderIndexReport(report);
    setStatus(
      elements.indexStatus,
      `${report.indexed_resources}/${report.discovered_resources} resources indexed · ${report.indexed_chunks} chunks`
    );
  });
}

async function handleRetrieve(): Promise<void> {
  const query = elements.retrieveQuery.value.trim();
  if (!query) {
    setStatus(elements.retrieveStatus, "Query required", "error");
    return;
  }
  await withBusy(elements.retrieveSubmit, elements.retrieveStatus, "Retrieving", async () => {
    const response = await api.retrieve({
      query,
      course_id: elements.retrieveCourse.value || null,
      kinds: selectedKinds(),
      top_k: numeric(elements.retrieveTopK.value, 8),
    });
    renderResults(response.results);
    setStatus(elements.retrieveStatus, `${response.results.length} results`);
  });
}

async function handleGenerate(): Promise<void> {
  const code = elements.generateCourse.value;
  if (!code) {
    setStatus(elements.generateStatus, "Select a course first", "error");
    return;
  }
  const course = discoveredCourses.find((c) => c.code === code);
  if (!course) {
    setStatus(elements.generateStatus, "Course not found", "error");
    return;
  }
  await withBusy(elements.generateSubmit, elements.generateStatus, "Generating", async () => {
    const base = {
      course_id: course.code,
      course_title: course.title,
      scope_notes: parseScopeNotes(elements.scopeNotes.value),
      top_k: numeric(elements.generateTopK.value, 40),
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
  if (!latestLatex) return;
  const code = elements.generateCourse.value || "studylens";
  const name = `${sanitizeFilename(`${code}-${generationMode}`)}.tex`;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([latestLatex], { type: "application/x-tex" }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

function activateView(view: string): void {
  elements.navItems.forEach((item) =>
    item.classList.toggle("active", item.dataset.view === view)
  );
  elements.views.forEach((panel) =>
    panel.classList.toggle("active", panel.id === `view-${view}`)
  );
}

function setGenerationMode(mode: "cheatsheet" | "exam"): void {
  generationMode = mode;
  elements.modeButtons.forEach((button) =>
    button.classList.toggle("active", button.dataset.mode === mode)
  );
  elements.questionCountField.classList.toggle("hidden", mode !== "exam");
}

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

function renderIndexReport(report: AutoIndexReport): void {
  elements.indexResults.replaceChildren(
    ...report.items.map((item) =>
      resultNode(
        item.title,
        item.error || item.local_path || item.source_url || "",
        autoIndexItemMeta(item)
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
  const number = Number.parseInt(value, 10);
  return Number.isFinite(number) ? number : fallback;
}

function setStatus(node: HTMLElement, value: string, mode: "ok" | "error" = "ok"): void {
  node.textContent = value;
  node.style.color = mode === "error" ? "var(--danger)" : "var(--muted)";
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing #${id}`);
  }
  return element as T;
}
