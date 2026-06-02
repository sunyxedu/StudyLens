import { StudyLensApi, StudyLensApiError } from "./api.js";
import {
  loadSettings,
  parseScopeNotes,
  resolveBackendUrl,
  sanitizeFilename,
} from "./state.js";
import {
  addMessage,
  buildQuestion,
  createConversation,
  loadConversations,
  saveConversations,
} from "./chat.js";
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
import { citationLabel, clippedText, formatSeconds, resultTitle, scoreLabel } from "./render.js";
import type {
  AuthSession,
  BrowserStateStatus,
  ChatMessage,
  Conversation,
  DiscoveredCourse,
  ResourceKind,
  SearchResult,
} from "./types.js";

const elements = {
  topbarUser: byId<HTMLElement>("topbar-user"),
  topbarUsername: byId<HTMLSpanElement>("topbar-username"),
  logoutBtn: byId<HTMLButtonElement>("logout-btn"),
  loginForm: byId<HTMLFormElement>("login-form"),
  loginTitle: byId<HTMLHeadingElement>("login-title"),
  loginSubtitle: byId<HTMLSpanElement>("login-subtitle"),
  authModeButtons: Array.from(document.querySelectorAll<HTMLButtonElement>(".auth-mode")),
  loginUsername: byId<HTMLInputElement>("login-username"),
  loginGradeField: byId<HTMLElement>("login-grade-field"),
  loginGrade: byId<HTMLInputElement>("login-grade"),
  loginCourseField: byId<HTMLElement>("login-course-field"),
  loginCourse: byId<HTMLInputElement>("login-course"),
  loginPassword: byId<HTMLInputElement>("login-password"),
  loginSubmit: byId<HTMLButtonElement>("login-submit"),
  loginStatus: byId<HTMLSpanElement>("login-status"),
  browserStateStart: byId<HTMLButtonElement>("browser-state-start"),
  browserStateNext: byId<HTMLButtonElement>("browser-state-next"),
  browserStateCancel: byId<HTMLButtonElement>("browser-state-cancel"),
  browserStateStatus: byId<HTMLSpanElement>("browser-state-status"),
  browserStateCount: byId<HTMLSpanElement>("browser-state-count"),
  browserStateStepKey: byId<HTMLSpanElement>("browser-state-step-key"),
  browserStateStepTitle: byId<HTMLHeadingElement>("browser-state-step-title"),
  browserStateInstruction: byId<HTMLParagraphElement>("browser-state-instruction"),
  browserStateUrl: byId<HTMLAnchorElement>("browser-state-url"),
  // Banner
  coursesBannerUsername: byId<HTMLSpanElement>("courses-banner-username"),
  coursesBannerSignout: byId<HTMLButtonElement>("courses-banner-signout"),
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
  // Ask / chat tab
  newConvBtn: byId<HTMLButtonElement>("new-conv-btn"),
  convList: byId<HTMLUListElement>("conv-list"),
  chatMessages: byId<HTMLElement>("chat-messages"),
  chatEmpty: byId<HTMLElement>("chat-empty"),
  askExercises: byId<HTMLInputElement>("ask-exercises"),
  askTopK: byId<HTMLInputElement>("ask-top-k"),
  askQuestion: byId<HTMLTextAreaElement>("ask-question"),
  askSubmit: byId<HTMLButtonElement>("ask-submit"),
  askStatus: byId<HTMLSpanElement>("ask-status"),
  // Generate tab
  modeButtons: Array.from(document.querySelectorAll<HTMLButtonElement>(".segment[data-mode]")),
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

const generateModeState: Record<"cheatsheet" | "exam", { scopeNotes: string; latex: string }> = {
  cheatsheet: { scopeNotes: "", latex: "" },
  exam: { scopeNotes: "", latex: "" },
};
let discoveredCourses: DiscoveredCourse[] = [];
let currentCourse: DiscoveredCourse | null = null;
let authSession: AuthSession | null = null;
let authMode: "register" | "login" = "register";
const selectedCourseCodes = new Set<string>();
let conversations: Conversation[] = [];
let activeConversation: Conversation | null = null;

// Nudge counter: threshold is uniform random with mean 10, hard cap 15
let nudgeCounter = 0;
let nudgeThreshold = nextNudgeThreshold();
let currentNudgeEl: HTMLElement | null = null;

function nextNudgeThreshold(): number {
  return Math.floor(Math.random() * 11) + 5; // [5, 15], mean ~10
}

const THUMBS_UP_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/><path d="M7 11l4-7a2.2 2.2 0 0 1 2 2v3h5.4a2 2 0 0 1 2 2.3l-1.1 6A2 2 0 0 1 17.3 20H7"/></svg>`;
const THUMBS_DOWN_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 13V4h3a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1z"/><path d="M17 13l-4 7a2.2 2.2 0 0 1-2-2v-3H5.6a2 2 0 0 1-2-2.3l1.1-6A2 2 0 0 1 6.7 4H17"/></svg>`;

const COPY_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>`;
const CHECK_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`;
const RETRY_SVG = `<svg class="retry-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v6h-6"/></svg>`;
const EDIT_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>`;

init();

function init(): void {
  const settings = loadSettings();
  settings.backendUrl = resolveBackendUrl(settings, window.location);
  api = new StudyLensApi(settings.backendUrl);

  elements.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void handleLogin();
  });
  elements.logoutBtn.addEventListener("click", () => { void handleLogout(); });
  elements.coursesBannerSignout.addEventListener("click", () => { void handleLogout(); });
  elements.authModeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setAuthMode(button.dataset.authMode === "login" ? "login" : "register");
    });
  });
  elements.browserStateStart.addEventListener("click", handleBrowserStateStart);
  elements.browserStateNext.addEventListener("click", handleBrowserStateNext);
  elements.browserStateCancel.addEventListener("click", handleBrowserStateCancel);
  elements.backToCoursesBtn.addEventListener("click", showCoursesPage);
  elements.reindexBtn.addEventListener("click", handleReindex);
  elements.newConvBtn.addEventListener("click", handleNewConversation);
  elements.chatMessages.addEventListener("click", handleChatAction);
  elements.askSubmit.addEventListener("click", () => { void handleSendMessage(); });
  elements.askQuestion.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void handleSendMessage(); }
  });
  elements.askQuestion.addEventListener("input", () => autoResizeTextarea(elements.askQuestion));
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
  setAuthMode("register");
  void initializeAuth();
}

// ── Auth and setup ────────────────────────────────────────────────────

async function initializeAuth(): Promise<void> {
  try {
    const session = await api.session();
    handleAuthenticated(session);
  } catch {
    showLoginView();
  }
}

async function handleLogin(): Promise<void> {
  const label = authMode === "register" ? "Registering" : "Signing in";
  await withBusy(elements.loginSubmit, elements.loginStatus, label, async () => {
    const credentials = {
      username: elements.loginUsername.value.trim(),
      password: elements.loginPassword.value,
    };
    const session = authMode === "register"
      ? await api.register({
          ...credentials,
          grade: elements.loginGrade.value.trim(),
          course: elements.loginCourse.value.trim(),
        })
      : await api.login(credentials);
    elements.loginPassword.value = "";
    handleAuthenticated(session);
  });
}

async function handleLogout(): Promise<void> {
  try {
    await api.logout();
  } finally {
    resetAuthenticatedState();
    setAuthMode("login");
    showLoginView();
  }
}

function handleAuthenticated(session: AuthSession): void {
  authSession = session;
  elements.topbarUsername.textContent = session.user.username;
  elements.topbarUser.classList.remove("hidden");
  elements.coursesBannerUsername.textContent = session.user.username;
  if (session.needs_browser_state) {
    void showBrowserStateView();
  } else {
    showCoursesApp();
  }
}

function showLoginView(): void {
  shell.classList.remove("mode-courses", "mode-setup");
  shell.classList.add("mode-login");
  elements.topbarUser.classList.add("hidden");
  activateTopLevelView("view-login");
  setStatus(elements.loginStatus, "");
  elements.loginUsername.focus();
}

function resetAuthenticatedState(): void {
  authSession = null;
  discoveredCourses = [];
  selectedCourseCodes.clear();
  currentCourse = null;
  conversations = [];
  activeConversation = null;
  latestLatex = "";
  generateModeState.cheatsheet = { scopeNotes: "", latex: "" };
  generateModeState.exam = { scopeNotes: "", latex: "" };

  elements.topbarUsername.textContent = "";
  elements.sidebarCourseContext.classList.add("hidden");
  elements.sidebarCourseNav.classList.add("hidden");
  elements.coursesSummary.textContent = "";
  elements.coursesList.replaceChildren();
  elements.coursesProgressList.replaceChildren();
  elements.coursesProgress.hidden = true;
  elements.convList.replaceChildren();
  Array.from(elements.chatMessages.children)
    .filter((node) => node !== elements.chatEmpty)
    .forEach((node) => node.remove());
  elements.chatEmpty.classList.remove("hidden");
  elements.scopeNotes.value = "";
  elements.latexOutput.textContent = "";
  elements.downloadLatex.disabled = true;
  elements.retrieveQuery.value = "";
  elements.retrieveResults.replaceChildren();
  setStatus(elements.coursesStatus, "");
  setStatus(elements.askStatus, "");
  setStatus(elements.generateStatus, "");
  setStatus(elements.retrieveStatus, "");
  setStatus(elements.reindexStatus, "");
  updateCoursesActions();
}

function handleAuthRequired(error: unknown): boolean {
  if (!isAuthRequiredError(error)) {
    return false;
  }
  resetAuthenticatedState();
  setAuthMode("login");
  showLoginView();
  setStatus(elements.loginStatus, "Please log in to continue.", "error");
  return true;
}

function isAuthRequiredError(error: unknown): boolean {
  return error instanceof StudyLensApiError
    && error.status === 401
    && error.detail === "authentication required";
}

function setAuthMode(mode: "register" | "login"): void {
  authMode = mode;
  const isRegister = mode === "register";
  elements.loginForm.dataset.authMode = mode;
  elements.authModeButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.authMode === mode);
  });
  elements.loginTitle.textContent = isRegister ? "Register" : "Login";
  elements.loginSubtitle.textContent = isRegister
    ? "Create your StudyLens account"
    : "Use an existing StudyLens account";
  elements.loginGradeField.classList.toggle("reserved", !isRegister);
  elements.loginCourseField.classList.toggle("reserved", !isRegister);
  elements.loginGrade.required = isRegister;
  elements.loginCourse.required = isRegister;
  elements.loginGrade.disabled = !isRegister;
  elements.loginCourse.disabled = !isRegister;
  elements.loginPassword.autocomplete = isRegister ? "new-password" : "current-password";
  elements.loginSubmit.textContent = isRegister ? "Register" : "Login";
  setStatus(elements.loginStatus, "");
}

async function showBrowserStateView(): Promise<void> {
  shell.classList.remove("mode-courses", "mode-login");
  shell.classList.add("mode-setup");
  activateTopLevelView("view-browser-state");
  try {
    renderBrowserStateStatus(await api.browserStateStatus());
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(
      elements.browserStateStatus,
      error instanceof Error ? error.message : "Setup status unavailable",
      "error"
    );
  }
}

function showCoursesApp(): void {
  shell.classList.remove("mode-login", "mode-setup");
  shell.classList.add("mode-courses");
  activateTopLevelView("view-courses");
  elements.sidebarCourseContext.classList.add("hidden");
  elements.sidebarCourseNav.classList.add("hidden");
  currentCourse = null;
  conversations = [];
  activeConversation = null;
  void loadCachedCourses();
}

function activateTopLevelView(id: string): void {
  document.querySelectorAll<HTMLElement>(".workspace > .view").forEach((view) => {
    view.classList.toggle("active", view.id === id);
  });
}

function handleBrowserStateStart(): void {
  void withSetupBusy(
    elements.browserStateStart,
    elements.browserStateStatus,
    "Opening",
    () => api.startBrowserState()
  );
}

function handleBrowserStateNext(): void {
  void withSetupBusy(
    elements.browserStateNext,
    elements.browserStateStatus,
    "Checking",
    () => api.advanceBrowserState(),
    (status) => {
      if (status.ready) {
        authSession = authSession
          ? {
              ...authSession,
              browser_state_ready: true,
              needs_browser_state: false,
            }
          : null;
        showCoursesApp();
      }
    }
  );
}

function handleBrowserStateCancel(): void {
  void withSetupBusy(
    elements.browserStateCancel,
    elements.browserStateStatus,
    "Closing",
    () => api.cancelBrowserState()
  );
}

function renderBrowserStateStatus(status: BrowserStateStatus): void {
  elements.browserStateStart.disabled = status.running;
  elements.browserStateNext.disabled = !status.running;
  elements.browserStateCancel.disabled = !status.running;

  if (status.step) {
    const humanIndex = (status.step_index ?? 0) + 1;
    elements.browserStateStepKey.textContent = status.step.key;
    elements.browserStateStepTitle.textContent = status.step.title;
    elements.browserStateInstruction.textContent = status.step.instruction;
    elements.browserStateCount.textContent = `${humanIndex}/${status.total_steps}`;
    elements.browserStateUrl.textContent = shortUrl(status.step.url);
    elements.browserStateUrl.href = status.step.url;
    elements.browserStateUrl.classList.remove("hidden");
    elements.browserStateNext.textContent =
      humanIndex === status.total_steps ? "Save cookies" : "Next site";
  } else {
    elements.browserStateStepKey.textContent = status.ready ? "Saved" : "Ready";
    elements.browserStateStepTitle.textContent = status.ready
      ? "Cookies saved"
      : "Connect course sites";
    elements.browserStateInstruction.textContent = status.ready
      ? "StudyLens can now process your course materials."
      : "Open the setup browser and sign into each site.";
    elements.browserStateCount.textContent = "";
    elements.browserStateUrl.classList.add("hidden");
    elements.browserStateNext.textContent = "Next";
  }

  if (status.error) {
    setStatus(elements.browserStateStatus, status.error, "error");
  } else if (status.ready) {
    setStatus(elements.browserStateStatus, "Saved");
  } else if (status.running) {
    setStatus(elements.browserStateStatus, "Browser is open");
  } else {
    setStatus(elements.browserStateStatus, "");
  }
}

// ── Navigation ────────────────────────────────────────────────────────

function showCoursesPage(): void {
  shell.classList.add("mode-courses");
  activateTopLevelView("view-courses");
  elements.sidebarCourseContext.classList.add("hidden");
  elements.sidebarCourseNav.classList.add("hidden");
  currentCourse = null;
  conversations = [];
  activeConversation = null;
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
  activateTopLevelView("view-course");
  activateCourseTab("ask");
  setStatus(elements.askStatus, "");
  initChatForCourse(course.code);
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
  } catch (error) {
    handleAuthRequired(error);
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
    } catch (error) {
      if (handleAuthRequired(error)) return null;
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
        if (handleAuthRequired(error)) {
          queue.length = 0;
          return;
        }
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
    if (handleAuthRequired(error)) return;
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

// ── Chat (Ask tab) ────────────────────────────────────────────────────

function initChatForCourse(courseId: string): void {
  conversations = loadConversations(courseId);
  if (conversations.length === 0) {
    conversations.push(createConversation(courseId));
    saveConversations(courseId, conversations);
  }
  selectConversation(conversations[0].id);
}

function selectConversation(id: string): void {
  const conv = conversations.find((c) => c.id === id);
  if (!conv) return;
  activeConversation = conv;
  nudgeCounter = 0;
  nudgeThreshold = nextNudgeThreshold();
  currentNudgeEl = null;
  renderConversationList();
  renderMessages(conv);
}

function handleNewConversation(): void {
  if (!currentCourse) return;
  const conv = createConversation(currentCourse.code);
  conversations.unshift(conv);
  saveConversations(currentCourse.code, conversations);
  selectConversation(conv.id);
}

function handleDeleteConversation(id: string): void {
  if (!currentCourse) return;
  conversations = conversations.filter((c) => c.id !== id);
  if (conversations.length === 0) {
    conversations.push(createConversation(currentCourse.code));
  }
  saveConversations(currentCourse.code, conversations);
  if (activeConversation?.id === id) {
    selectConversation(conversations[0].id);
  } else {
    renderConversationList();
  }
}

async function handleSendMessage(): Promise<void> {
  if (!currentCourse || !activeConversation) return;
  const question = elements.askQuestion.value.trim();
  if (!question) return;
  elements.askQuestion.value = "";
  autoResizeTextarea(elements.askQuestion);
  void sendQuestion(question);
}

async function sendQuestion(question: string): Promise<void> {
  if (!currentCourse || !activeConversation) return;
  autoDismissCurrentNudge();

  // Build context from existing history before adding the new message.
  const contextQuestion = buildQuestion(activeConversation, question);

  const userMsg = addMessage(activeConversation, { role: "user", content: question, citations: [] });
  saveConversations(currentCourse.code, conversations);
  renderConversationList();
  appendMessageBubble(userMsg);

  const thinkingEl = createThinkingEl();
  elements.chatMessages.appendChild(thinkingEl);
  scrollChatToBottom();

  elements.askSubmit.disabled = true;
  setStatus(elements.askStatus, "");


  try {
    const answer = await api.ask({
      question: contextQuestion,
      course_id: currentCourse.code,
      top_k: numeric(elements.askTopK.value, 5),
      include_exercises: elements.askExercises.checked,
    });
    thinkingEl.remove();
    const assistantMsg = addMessage(activeConversation, {
      role: "assistant",
      content: answer.answer,
      citations: answer.citations,
    });
    saveConversations(currentCourse.code, conversations);
    appendMessageBubble(assistantMsg);
    scrollChatToBottom();
  } catch (error) {
    thinkingEl.remove();
    if (handleAuthRequired(error)) return;
    setStatus(elements.askStatus, error instanceof Error ? error.message : "Request failed", "error");
  } finally {
    elements.askSubmit.disabled = false;
  }
}

function renderConversationList(): void {
  elements.convList.replaceChildren(
    ...conversations.map((conv) => {
      const li = document.createElement("li");
      li.className = `conv-item${conv.id === activeConversation?.id ? " active" : ""}`;

      const title = document.createElement("span");
      title.className = "conv-item-title";
      title.textContent = conv.title;
      title.title = conv.title;

      const time = document.createElement("span");
      time.className = "conv-item-time";
      time.textContent = relativeTime(conv.updatedAt);

      const del = document.createElement("button");
      del.type = "button";
      del.className = "conv-delete-btn";
      del.textContent = "×";
      del.title = "Delete";
      del.addEventListener("click", (e) => { e.stopPropagation(); handleDeleteConversation(conv.id); });

      li.append(title, time, del);
      li.addEventListener("click", () => selectConversation(conv.id));
      return li;
    })
  );
}

function renderMessages(conv: Conversation): void {
  const existing = Array.from(elements.chatMessages.children).filter(
    (n) => n !== elements.chatEmpty
  );
  existing.forEach((n) => n.remove());

  const isEmpty = conv.messages.length === 0;
  elements.chatEmpty.classList.toggle("hidden", !isEmpty);

  if (!isEmpty) {
    conv.messages.forEach((msg) => {
      elements.chatMessages.appendChild(createMessageEl(msg));
    });
    scrollChatToBottom();
  }
}

function appendMessageBubble(msg: ChatMessage): void {
  elements.chatEmpty.classList.add("hidden");
  elements.chatMessages.appendChild(createMessageEl(msg));
  if (msg.role === "assistant") maybeInsertNudge();
}

function maybeInsertNudge(): void {
  nudgeCounter++;
  if (nudgeCounter < nudgeThreshold) return;
  nudgeCounter = 0;
  nudgeThreshold = nextNudgeThreshold();
  const nudge = createNudgeEl();
  elements.chatMessages.appendChild(nudge);
  scrollNudgeIntoView(nudge);
}

function createNudgeEl(): HTMLElement {
  const nudge = document.createElement("div");
  nudge.className = "chat-feedback-nudge";
  nudge.setAttribute("role", "group");
  nudge.setAttribute("aria-label", "Answer satisfaction feedback");
  nudge.dataset.voted = "false";
  nudge.innerHTML = `
    <span class="chat-feedback-prompt">How are the answers working for you?</span>
    <div class="chat-feedback-actions">
      <button class="chat-feedback-btn good" type="button" data-vote="up">
        ${THUMBS_UP_SVG}Helpful
      </button>
      <button class="chat-feedback-btn bad" type="button" data-vote="down">
        ${THUMBS_DOWN_SVG}Not helpful
      </button>
    </div>
  `;
  nudge.querySelectorAll<HTMLButtonElement>(".chat-feedback-btn").forEach((btn) => {
    btn.addEventListener("click", () => handleNudgeVote(nudge, btn.dataset.vote ?? ""));
  });
  currentNudgeEl = nudge;
  return nudge;
}

function handleNudgeVote(nudge: HTMLElement, vote: string): void {
  nudge.dataset.voted = "true";
  if (currentNudgeEl === nudge) currentNudgeEl = null;
  nudge.innerHTML = `<span class="chat-feedback-thanks">${CHECK_SVG}Thanks for the feedback!</span>`;
  console.info("[StudyLens] feedback vote:", vote);
  setTimeout(() => dismissNudge(nudge), 2000);
}

function dismissNudge(nudge: HTMLElement): void {
  nudge.classList.add("is-dismissed");
}

function autoDismissCurrentNudge(): void {
  if (currentNudgeEl && currentNudgeEl.dataset.voted === "false") {
    dismissNudge(currentNudgeEl);
    currentNudgeEl = null;
  }
}

function createMessageEl(msg: ChatMessage): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = `chat-msg chat-msg-${msg.role}`;
  wrap.dataset.msgId = msg.id;

  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  if (msg.role === "user") {
    bubble.textContent = msg.content;
  } else {
    bubble.innerHTML = renderAnswer(msg.content);
  }
  wrap.appendChild(bubble);

  if (msg.citations.length > 0) {
    const cites = document.createElement("div");
    cites.className = "chat-citations";
    msg.citations.forEach((c, i) => {
      const chip = document.createElement("a");
      chip.className = "chat-citation-chip";
      chip.textContent = citationLabel(c, i);
      chip.title = citationLabel(c, i);
      const url = buildCitationUrl(c);
      if (url) { chip.href = url; chip.target = "_blank"; chip.rel = "noopener noreferrer"; }
      cites.appendChild(chip);
    });
    wrap.appendChild(cites);
  }

  if (msg.role === "assistant") {
    const bar = document.createElement("div");
    bar.className = "chat-msg-actions";
    bar.innerHTML = `<button class="chat-action-btn" type="button" aria-label="Copy answer" title="Copy" data-action="copy">${COPY_SVG}</button><button class="chat-action-btn" type="button" aria-label="Regenerate answer" title="Retry" data-action="retry">${RETRY_SVG}</button>`;
    wrap.appendChild(bar);
  }

  if (msg.role === "user") {
    const bar = document.createElement("div");
    bar.className = "chat-msg-actions chat-msg-actions--user";
    bar.innerHTML = `<button class="chat-action-btn" type="button" aria-label="Edit message" title="Edit" data-action="edit">${EDIT_SVG}</button><button class="chat-action-btn" type="button" aria-label="Copy message" title="Copy" data-action="copy">${COPY_SVG}</button>`;
    wrap.appendChild(bar);
  }

  return wrap;
}

function handleChatAction(e: MouseEvent): void {
  const btn = (e.target as Element).closest<HTMLButtonElement>("[data-action]");
  if (!btn) return;
  const action = btn.dataset.action;
  const msgEl = btn.closest<HTMLElement>(".chat-msg");
  if (!msgEl) return;

  if (action === "copy") {
    const msgId = msgEl.dataset.msgId;
    const text = activeConversation?.messages.find((m) => m.id === msgId)?.content
      ?? msgEl.querySelector<HTMLElement>(".chat-bubble")?.innerText
      ?? "";
    void navigator.clipboard.writeText(text).then(() => {
      btn.innerHTML = CHECK_SVG;
      btn.classList.add("is-copied");
      setTimeout(() => {
        btn.innerHTML = COPY_SVG;
        btn.classList.remove("is-copied");
      }, 1200);
    }).catch(() => { /* clipboard unavailable */ });
  }

  if (action === "edit") {
    const bubble = msgEl.querySelector<HTMLElement>(".chat-bubble");
    const bar = msgEl.querySelector<HTMLElement>(".chat-msg-actions");
    if (!bubble || !bar) return;
    const original = bubble.textContent ?? "";
    bubble.classList.add("is-editing");
    bubble.contentEditable = "true";
    bubble.focus();
    // Move caret to end
    const range = document.createRange();
    range.selectNodeContents(bubble);
    range.collapse(false);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);

    bar.classList.add("hidden");

    const editActions = document.createElement("div");
    editActions.className = "chat-edit-actions";
    editActions.innerHTML = `<button class="chat-edit-btn cancel" type="button">Cancel</button><button class="chat-edit-btn save" type="button">Save &amp; resend</button>`;
    msgEl.appendChild(editActions);

    editActions.querySelector(".cancel")!.addEventListener("click", () => {
      bubble.textContent = original;
      bubble.classList.remove("is-editing");
      bubble.contentEditable = "false";
      bar.classList.remove("hidden");
      editActions.remove();
    });

    editActions.querySelector(".save")!.addEventListener("click", () => {
      const edited = bubble.textContent?.trim() ?? "";
      if (!edited) return;
      bubble.classList.remove("is-editing");
      bubble.contentEditable = "false";
      bar.classList.remove("hidden");
      editActions.remove();

      if (!activeConversation || !currentCourse) return;
      const msgId = msgEl.dataset.msgId;
      const idx = activeConversation.messages.findIndex((m) => m.id === msgId);
      if (idx < 0) return;
      // Remove the user message and everything after it; sendQuestion re-adds it
      activeConversation.messages.splice(idx);
      saveConversations(currentCourse.code, conversations);
      renderMessages(activeConversation);
      void sendQuestion(edited);
    });
    return;
  }

  if (action === "retry") {
    if (!activeConversation || !currentCourse) return;
    const msgId = msgEl.dataset.msgId;
    const idx = activeConversation.messages.findIndex((m) => m.id === msgId);
    if (idx < 1) return;
    const preceding = activeConversation.messages[idx - 1];
    if (preceding.role !== "user") return;

    // Spin the icon for one rotation
    const ico = btn.querySelector<SVGElement>(".retry-ico");
    if (ico) {
      ico.classList.remove("is-spinning");
      void (ico as unknown as HTMLElement).offsetWidth; // force reflow to restart animation
      ico.classList.add("is-spinning");
    }

    // Truncate conversation to just before this assistant message, then re-send
    activeConversation.messages.splice(idx - 1);
    saveConversations(currentCourse.code, conversations);
    renderMessages(activeConversation);
    void sendQuestion(preceding.content);
  }
}

function createThinkingEl(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "chat-msg chat-msg-assistant";
  wrap.innerHTML = `<div class="chat-thinking"><span>Thinking</span><div class="chat-thinking-dots"><span></span><span></span><span></span></div></div>`;
  return wrap;
}

function scrollChatToBottom(): void {
  requestAnimationFrame(() => {
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
  });
}

function scrollNudgeIntoView(nudge: HTMLElement): void {
  // Two rAFs: first lets the browser paint the nudge, second measures accurate rects.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const cRect = elements.chatMessages.getBoundingClientRect();
      const nRect = nudge.getBoundingClientRect();
      if (nRect.bottom > cRect.bottom) {
        elements.chatMessages.scrollTop += nRect.bottom - cRect.bottom + 16;
      }
    });
  });
}

function autoResizeTextarea(ta: HTMLTextAreaElement): void {
  ta.style.height = "auto";
  ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
}

function relativeTime(ts: number): string {
  const diff = Date.now() - ts;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
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
  // Persist current mode's state before switching.
  generateModeState[generationMode] = {
    scopeNotes: elements.scopeNotes.value,
    latex: latestLatex,
  };

  generationMode = mode;
  elements.modeButtons.forEach((button) =>
    button.classList.toggle("active", button.dataset.mode === mode)
  );
  elements.questionCountField.classList.toggle("hidden", mode !== "exam");

  // Restore the new mode's state.
  const saved = generateModeState[mode];
  elements.scopeNotes.value = saved.scopeNotes;
  latestLatex = saved.latex;
  elements.latexOutput.textContent = saved.latex;
  elements.downloadLatex.disabled = !saved.latex;
  setStatus(elements.generateStatus, "");
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

function buildCitationUrl(c: { source_url?: string | null; start_seconds?: number | null; page?: number | null }): string | null {
  if (!c.source_url) return null;
  if (c.start_seconds != null) {
    const sep = c.source_url.includes("?") ? "&" : "?";
    return `${c.source_url}${sep}start=${Math.floor(c.start_seconds)}`;
  }
  if (c.page != null) return `${c.source_url}#page=${c.page}`;
  return c.source_url;
}

function buildChunkUrl(chunk: SearchResult["chunk"]): string | null {
  const startSeconds = chunk.metadata["start_seconds"];
  const page = chunk.metadata["page"];
  return buildCitationUrl({
    source_url: chunk.source_url,
    start_seconds: typeof startSeconds === "number" ? startSeconds : null,
    page: typeof page === "number" ? page : null,
  });
}

function chunkLocator(chunk: SearchResult["chunk"]): string {
  const startSeconds = chunk.metadata["start_seconds"];
  const page = chunk.metadata["page"];
  if (typeof startSeconds === "number") return ` · ${formatSeconds(startSeconds)}`;
  if (typeof page === "number") return ` · p.${page}`;
  return ` · chunk ${chunk.position}`;
}

function renderResults(results: SearchResult[]): void {
  elements.retrieveResults.replaceChildren(
    ...results.map((result) =>
      resultNode(
        resultTitle(result),
        clippedText(result.chunk.text),
        `${result.chunk.kind} · ${scoreLabel(result.score)}${chunkLocator(result.chunk)}`,
        buildChunkUrl(result.chunk),
      )
    )
  );
}

function resultNode(title: string, text: string, meta: string, url?: string | null): HTMLElement {
  const article = document.createElement("article");
  article.className = "result-item";

  const header = document.createElement("div");
  header.className = "result-meta";

  let titleEl: HTMLElement;
  if (url) {
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.className = "result-title result-title-link";
    a.textContent = title;
    titleEl = a;
  } else {
    const span = document.createElement("span");
    span.className = "result-title";
    span.textContent = title;
    titleEl = span;
  }

  const metaNode = document.createElement("span");
  metaNode.textContent = meta;

  const body = document.createElement("p");
  body.className = "result-text";
  body.textContent = text;

  header.append(titleEl, metaNode);
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
    if (handleAuthRequired(error)) return;
    setStatus(status, error instanceof Error ? error.message : "Request failed", "error");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function withSetupBusy(
  button: HTMLButtonElement,
  statusNode: HTMLElement,
  label: string,
  action: () => Promise<BrowserStateStatus>,
  afterRender?: (status: BrowserStateStatus) => void
): Promise<void> {
  const original = button.textContent || "";
  button.disabled = true;
  button.textContent = label;
  setStatus(statusNode, label);
  try {
    const status = await action();
    renderBrowserStateStatus(status);
    afterRender?.(status);
  } catch (error) {
    button.disabled = false;
    if (handleAuthRequired(error)) return;
    setStatus(statusNode, error instanceof Error ? error.message : "Request failed", "error");
  } finally {
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
