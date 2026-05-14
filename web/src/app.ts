import { StudyLensApi, normalizeBaseUrl } from "./api.js";
import {
  loadSettings,
  parseScopeNotes,
  resolveBackendUrl,
  sanitizeFilename,
  saveSettings,
} from "./state.js";
import { autoIndexItemMeta, citationLabel, clippedText, resultTitle, scoreLabel } from "./render.js";
import type { AutoIndexReport, ResourceKind, SearchResult } from "./types.js";

const elements = {
  backendUrl: byId<HTMLInputElement>("backend-url"),
  saveBackend: byId<HTMLButtonElement>("save-backend"),
  healthPill: byId<HTMLSpanElement>("health-pill"),
  courseId: byId<HTMLInputElement>("course-id"),
  courseTitle: byId<HTMLInputElement>("course-title"),
  navItems: Array.from(document.querySelectorAll<HTMLButtonElement>(".nav-item")),
  views: Array.from(document.querySelectorAll<HTMLElement>(".view")),
  askExercises: byId<HTMLInputElement>("ask-exercises"),
  askTopK: byId<HTMLInputElement>("ask-top-k"),
  askQuestion: byId<HTMLTextAreaElement>("ask-question"),
  askSubmit: byId<HTMLButtonElement>("ask-submit"),
  askStatus: byId<HTMLSpanElement>("ask-status"),
  answerCard: byId<HTMLElement>("answer-card"),
  citationList: byId<HTMLElement>("citation-list"),
  indexSubmit: byId<HTMLButtonElement>("index-submit"),
  indexStatus: byId<HTMLSpanElement>("index-status"),
  indexResults: byId<HTMLElement>("index-results"),
  retrieveTopK: byId<HTMLInputElement>("retrieve-top-k"),
  retrieveQuery: byId<HTMLTextAreaElement>("retrieve-query"),
  retrieveKinds: byId<HTMLElement>("retrieve-kinds"),
  retrieveSubmit: byId<HTMLButtonElement>("retrieve-submit"),
  retrieveStatus: byId<HTMLSpanElement>("retrieve-status"),
  retrieveResults: byId<HTMLElement>("retrieve-results"),
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

init();

function init(): void {
  const settings = loadSettings();
  settings.backendUrl = resolveBackendUrl(settings, window.location);
  elements.backendUrl.value = settings.backendUrl;
  elements.courseId.value = settings.courseId;
  elements.courseTitle.value = settings.courseTitle;
  api = new StudyLensApi(settings.backendUrl);

  elements.navItems.forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.view || "ask"));
  });
  elements.modeButtons.forEach((button) => {
    button.addEventListener("click", () => setGenerationMode(button.dataset.mode === "exam" ? "exam" : "cheatsheet"));
  });
  elements.saveBackend.addEventListener("click", handleSaveSettings);
  elements.courseId.addEventListener("change", handleSaveSettings);
  elements.courseTitle.addEventListener("change", handleSaveSettings);
  elements.askSubmit.addEventListener("click", handleAsk);
  elements.indexSubmit.addEventListener("click", handleIndex);
  elements.retrieveSubmit.addEventListener("click", handleRetrieve);
  elements.generateSubmit.addEventListener("click", handleGenerate);
  elements.downloadLatex.addEventListener("click", handleDownloadLatex);

  void refreshHealth();
}

function handleSaveSettings(): void {
  const settings = currentSettings();
  elements.backendUrl.value = settings.backendUrl;
  api = new StudyLensApi(settings.backendUrl);
  saveSettings(settings);
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
      course_id: courseIdOrNull(),
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

async function handleIndex(): Promise<void> {
  await withBusy(elements.indexSubmit, elements.indexStatus, "Syncing", async () => {
    const report = await api.autoIndexCourse({
      course_id: requireCourseId(),
      course_title: requireCourseTitle(),
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
      course_id: courseIdOrNull(),
      kinds: selectedKinds(),
      top_k: numeric(elements.retrieveTopK.value, 8),
    });
    renderResults(response.results);
    setStatus(elements.retrieveStatus, `${response.results.length} results`);
  });
}

async function handleGenerate(): Promise<void> {
  await withBusy(elements.generateSubmit, elements.generateStatus, "Generating", async () => {
    const base = {
      course_id: requireCourseId(),
      course_title: requireCourseTitle(),
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
  if (!latestLatex) {
    return;
  }
  const name = `${sanitizeFilename(`${requireCourseId()}-${generationMode}`)}.tex`;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([latestLatex], { type: "application/x-tex" }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

function activateView(view: string): void {
  elements.navItems.forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  elements.views.forEach((panel) => panel.classList.toggle("active", panel.id === `view-${view}`));
}

function setGenerationMode(mode: "cheatsheet" | "exam"): void {
  generationMode = mode;
  elements.modeButtons.forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
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

async function withBusy(button: HTMLButtonElement, status: HTMLElement, label: string, action: () => Promise<void>): Promise<void> {
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
  return Array.from(elements.retrieveKinds.querySelectorAll<HTMLInputElement>("input:checked")).map(
    (input) => input.value as ResourceKind
  );
}

function currentSettings() {
  return {
    backendUrl: normalizeBaseUrl(elements.backendUrl.value),
    courseId: elements.courseId.value.trim(),
    courseTitle: elements.courseTitle.value.trim(),
  };
}

function courseIdOrNull(): string | null {
  return elements.courseId.value.trim() || null;
}

function requireCourseId(): string {
  const courseId = elements.courseId.value.trim();
  if (!courseId) {
    throw new Error("Course ID required");
  }
  return courseId;
}

function requireCourseTitle(): string {
  const title = elements.courseTitle.value.trim();
  if (!title) {
    throw new Error("Course title required");
  }
  return title;
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
