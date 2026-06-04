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
  Citation,
  Conversation,
  DiscoveredCourse,
  ForumBoard,
  ForumCategoryWithBoards,
  ForumIndexResponse,
  ForumReply,
  ForumThread,
  ForumThreadSummary,
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
  browserStateSkip: byId<HTMLButtonElement>("browser-state-skip"),
  browserStateCancel: byId<HTMLButtonElement>("browser-state-cancel"),
  browserStateStatus: byId<HTMLSpanElement>("browser-state-status"),
  browserStateCount: byId<HTMLSpanElement>("browser-state-count"),
  browserStateStepKey: byId<HTMLSpanElement>("browser-state-step-key"),
  browserStateStepTitle: byId<HTMLHeadingElement>("browser-state-step-title"),
  browserStateInstruction: byId<HTMLParagraphElement>("browser-state-instruction"),
  browserStateUrl: byId<HTMLAnchorElement>("browser-state-url"),
  // Course library (main page)
  coursesDiscover: byId<HTMLButtonElement>("courses-discover"),
  coursesIndex: byId<HTMLButtonElement>("courses-index"),
  coursesSelectAll: byId<HTMLButtonElement>("courses-select-all"),
  coursesStatus: byId<HTMLSpanElement>("courses-status"),
  coursesSummary: byId<HTMLSpanElement>("courses-summary"),
  coursesList: byId<HTMLUListElement>("courses-list"),
  coursesProgress: byId<HTMLElement>("courses-progress"),
  coursesProgressList: byId<HTMLUListElement>("courses-progress-list"),
  forumOpen: byId<HTMLButtonElement>("forum-open"),
  forumStatus: byId<HTMLSpanElement>("forum-status"),
  forumBreadcrumb: byId<HTMLElement>("forum-breadcrumb"),
  forumEyebrow: byId<HTMLSpanElement>("forum-eyebrow"),
  forumPageTitle: byId<HTMLHeadingElement>("forum-page-title"),
  forumHome: byId<HTMLElement>("forum-home"),
  forumCategoryView: byId<HTMLElement>("forum-category-view"),
  forumBoardView: byId<HTMLElement>("forum-board-view"),
  forumThreadView: byId<HTMLElement>("forum-thread-view"),
  forumCompose: byId<HTMLElement>("forum-compose"),
  forumComposeCancel: byId<HTMLButtonElement>("forum-compose-cancel"),
  forumComposeSubmit: byId<HTMLButtonElement>("forum-compose-submit"),
  forumComposeCategorySel: byId<HTMLSelectElement>("forum-compose-category-sel"),
  forumBoardPickerInput: byId<HTMLInputElement>("forum-board-picker-input"),
  forumBoardPickerMenu: byId<HTMLElement>("forum-board-picker-menu"),
  forumBoardPickerError: byId<HTMLElement>("forum-board-picker-error"),
  forumComposeTitle: byId<HTMLInputElement>("forum-compose-title"),
  forumComposeBody: byId<HTMLElement>("forum-compose-body"),

  forumComposeAnon: byId<HTMLInputElement>("forum-compose-anon"),
  forumComposeCancel2: byId<HTMLButtonElement>("forum-compose-cancel-2"),
  forumComposeSubmit2: byId<HTMLButtonElement>("forum-compose-submit-2"),
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
let forumData: ForumIndexResponse | null = null;
let currentForumCategory: ForumCategoryWithBoards | null = null;
let currentForumBoard: ForumBoard | null = null;
let currentForumThread: ForumThread | null = null;
let forumComposeTargetBoardId: number | null = null;
let forumComposeSelectedBoard: { id: number; name: string } | null = null;

// ── Course card accent palette ────────────────────────────────────────
const CARD_ACCENTS = [
  "#2e5d4d", "#34706a", "#6f7f55", "#4c6e41", "#867a3b",
  "#b07d35", "#b1633a", "#9c5235", "#8a5560", "#7a6076",
  "#566884", "#5d7384",
];

function courseAccent(code: string): string {
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (Math.imul(31, h) + code.charCodeAt(i)) | 0;
  return CARD_ACCENTS[Math.abs(h) % CARD_ACCENTS.length];
}
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
  elements.authModeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      setAuthMode(button.dataset.authMode === "login" ? "login" : "register");
    });
  });
  elements.browserStateStart.addEventListener("click", handleBrowserStateStart);
  elements.browserStateNext.addEventListener("click", handleBrowserStateNext);
  elements.browserStateSkip.addEventListener("click", showCoursesApp);
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
  elements.forumOpen.addEventListener("click", openForum);
  elements.forumComposeCancel.addEventListener("click", closeForumCompose);
  elements.forumComposeCancel2.addEventListener("click", closeForumCompose);
  elements.forumComposeSubmit.addEventListener("click", () => { void handleCreateForumThread(); });
  elements.forumComposeSubmit2.addEventListener("click", () => { void handleCreateForumThread(); });
  elements.forumComposeCategorySel.addEventListener("change", () => {
    resetBoardPicker();
    updateForumComposeSubmitState();
  });
  elements.forumBoardPickerInput.addEventListener("focus", () => renderBoardPickerMenu());
  elements.forumBoardPickerInput.addEventListener("input", () => {
    forumComposeSelectedBoard = null;
    clearBoardPickerError();
    renderBoardPickerMenu();
    updateForumComposeSubmitState();
  });
  elements.forumBoardPickerInput.addEventListener("blur", () => {
    setTimeout(() => {
      elements.forumBoardPickerMenu.classList.add("hidden");
      validateBoardPicker();
    }, 160);
  });
  elements.forumComposeTitle.addEventListener("input", updateForumComposeSubmitState);
  setupMentionEditor(elements.forumComposeBody, updateForumComposeSubmitState);
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
  forumData = null;
  currentForumCategory = null;
  currentForumBoard = null;
  currentForumThread = null;
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
  elements.forumHome.replaceChildren();
  elements.forumCategoryView.replaceChildren();
  elements.forumBoardView.replaceChildren();
  elements.forumThreadView.replaceChildren();
  elements.forumBreadcrumb.replaceChildren();
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
  showToast(`Opening ${course.code} — ${stripCodePrefix(course.title)}`);
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
    const latest = courses.reduce<string | null>(
      (acc, c) => (c.updated_at && (!acc || c.updated_at > acc) ? c.updated_at : acc),
      null
    );
    updateCoursesSummary(0, latest);
    updateCoursesActions();
  } catch (error) {
    handleAuthRequired(error);
  }
}

async function handleDiscoverCourses(): Promise<void> {
  await withBusy(elements.coursesDiscover, elements.coursesStatus, "Discovering…", async () => {
    const response = await api.discoverCourses();
    discoveredCourses = response.courses;
    selectedCourseCodes.clear();
    renderCourseList();
    elements.coursesProgressList.replaceChildren();
    elements.coursesProgress.hidden = true;
    if (response.error) {
      setStatus(elements.coursesStatus, response.error, "error");
    } else {
      showToast(`Loaded ${response.courses.length} courses from EdStem`);
    }
    updateCoursesSummary(response.dropped_titles.length, response.error ? null : "just now");
    updateCoursesActions();
  });
}

function showToast(msg: string, durationMs = 2600): void {
  const el = document.createElement("div");
  el.className = "toast";
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => {
    el.classList.add("toast-out");
    el.addEventListener("animationend", () => el.remove(), { once: true });
  }, durationMs);
}

function renderCourseList(): void {
  const cards = discoveredCourses.map((course) => createCourseCard(course));
  elements.coursesList.replaceChildren(...cards);
  // Staggered entrance (~45ms per card)
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  cards.forEach((card, i) => {
    if (reduced) {
      card.classList.add("ccard--visible");
    } else {
      setTimeout(() => card.classList.add("ccard--visible"), i * 45);
    }
  });
}

function createCourseCard(course: DiscoveredCourse): HTMLLIElement {
  const accent = courseAccent(course.code);
  const selected = selectedCourseCodes.has(course.code);

  const li = document.createElement("li");
  li.className = `ccard${selected ? " ccard--selected" : ""}`;
  li.style.setProperty("--ccard-accent", accent);
  li.dataset.code = course.code;

  // Click card = toggle selection (Enter button stops propagation)
  li.addEventListener("click", () => {
    const isSelected = selectedCourseCodes.has(course.code);
    if (isSelected) {
      selectedCourseCodes.delete(course.code);
      li.classList.remove("ccard--selected");
    } else {
      selectedCourseCodes.add(course.code);
      li.classList.add("ccard--selected");
    }
    updateCoursesActions();
  });

  // Left accent bar
  const bar = document.createElement("div");
  bar.className = "ccard-bar";

  // Body
  const body = document.createElement("div");
  body.className = "ccard-body";

  // Head row
  const head = document.createElement("div");
  head.className = "ccard-head";

  const codePill = document.createElement("span");
  codePill.className = "ccard-code";
  codePill.textContent = course.code;

  const badge = document.createElement("span");
  badge.dataset.role = "badge";
  if (course.indexed_at) {
    badge.className = "ccard-badge ccard-badge--ready";
    badge.innerHTML = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M2 6l3 3 5-5"/></svg>Ready`;
  } else {
    badge.className = "ccard-badge ccard-badge--pending";
    badge.innerHTML = `<svg width="9" height="9" viewBox="0 0 10 10" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="5" cy="5" r="4"/></svg>Pending`;
  }

  head.append(codePill, badge);

  // Title
  const titleEl = document.createElement("div");
  titleEl.className = "ccard-title";
  titleEl.textContent = stripCodePrefix(course.title);
  titleEl.title = course.title;

  // Foot row
  const foot = document.createElement("div");
  foot.className = "ccard-foot";

  const meta = document.createElement("span");
  meta.className = "ccard-meta";
  meta.dataset.role = "meta";

  if (course.indexed_at) {
    meta.innerHTML = `<span class="ccard-meta-check">✓</span> Processed · ${formatTimestamp(course.indexed_at)}`;
  } else if (course.edstem_url) {
    meta.textContent = shortUrl(course.edstem_url);
    meta.title = course.edstem_url;
  }

  foot.append(meta);

  if (course.indexed_at) {
    const enterBtn = document.createElement("button");
    enterBtn.type = "button";
    enterBtn.className = "ccard-enter";
    enterBtn.innerHTML = `Enter <span class="ccard-enter-arrow">→</span>`;
    enterBtn.addEventListener("click", (e) => { e.stopPropagation(); enterCourse(course); });
    foot.append(enterBtn);
  }

  body.append(head, titleEl, foot);
  li.append(bar, body);
  return li;
}

function updateCoursesSummary(dropped: number = 0, refreshedAt?: string | null): void {
  if (discoveredCourses.length === 0) {
    elements.coursesSummary.textContent = "";
    return;
  }
  const dropNote = dropped > 0 ? ` · ${dropped} without a code skipped` : "";
  const refreshNote = refreshedAt
    ? ` · refreshed ${refreshedAt === "just now" ? "just now" : formatTimestamp(refreshedAt)}`
    : "";
  elements.coursesSummary.textContent = `${discoveredCourses.length} courses${dropNote}${refreshNote}`;
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
  // Update card visual state without full re-render
  elements.coursesList.querySelectorAll<HTMLElement>(".ccard").forEach((card) => {
    const code = card.dataset.code ?? "";
    card.classList.toggle("ccard--selected", selectedCourseCodes.has(code));
  });
  updateCoursesActions();
}

// ── Forum ─────────────────────────────────────────────────────────────

function forumAccent(id: number): string {
  return CARD_ACCENTS[Math.abs(id) % CARD_ACCENTS.length];
}

// ── Navigation ────────────────────────────────────────────────────────

function openForum(): void {
  shell.classList.remove("mode-login", "mode-setup");
  shell.classList.add("mode-courses");
  elements.sidebarCourseContext.classList.add("hidden");
  elements.sidebarCourseNav.classList.add("hidden");
  activateTopLevelView("view-forum");
  currentCourse = null;
  void loadForum();
}

async function loadForum(): Promise<void> {
  setStatus(elements.forumStatus, "Loading");
  try {
    forumData = await api.forumIndex();
    if (currentForumThread) {
      const refreshed = await api.forumThread(currentForumThread.id);
      renderForumThread(refreshed);
    } else if (currentForumBoard) {
      const resp = await api.forumBoard(currentForumBoard.id);
      renderForumBoard(resp.board, resp.threads);
    } else if (currentForumCategory) {
      const cat = forumCategories().find((c) => c.id === currentForumCategory!.id);
      if (cat) renderForumCategory(cat); else renderForumHome();
    } else {
      renderForumHome();
    }
    setStatus(elements.forumStatus, "");
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Forum failed", "error");
  }
}

function showForumPanel(panel: "home" | "category" | "board" | "thread"): void {
  elements.forumHome.classList.toggle("hidden", panel !== "home");
  elements.forumCategoryView.classList.toggle("hidden", panel !== "category");
  elements.forumBoardView.classList.toggle("hidden", panel !== "board");
  elements.forumThreadView.classList.toggle("hidden", panel !== "thread");
  elements.forumCompose.classList.add("hidden");
  renderForumBreadcrumb(panel);
}

// ── Breadcrumb (Slice 2) ──────────────────────────────────────────────

function renderForumBreadcrumb(panel: "home" | "category" | "board" | "thread"): void {
  const frag = document.createDocumentFragment();

  // Always-present "← My Courses" exit button
  const courses = document.createElement("button");
  courses.type = "button";
  courses.className = "forum-back-pill forum-back-pill--courses";
  courses.textContent = "← My Courses";
  courses.addEventListener("click", showCoursesPage);
  frag.appendChild(courses);

  if (panel !== "home") {
    const sep0 = document.createElement("span");
    sep0.className = "forum-bc-sep";
    sep0.setAttribute("aria-hidden", "true");
    sep0.textContent = "·";
    frag.appendChild(sep0);

    const back = document.createElement("button");
    back.type = "button";
    back.className = "forum-back-pill";
    back.textContent = "← Back";
    back.addEventListener("click", () => {
      if (panel === "thread" && currentForumBoard) void loadForumBoard(currentForumBoard.id);
      else if (panel === "board" && currentForumCategory) renderForumCategory(currentForumCategory);
      else renderForumHome();
    });
    frag.appendChild(back);
    const sep = document.createElement("span");
    sep.className = "forum-bc-sep";
    sep.setAttribute("aria-hidden", "true");
    sep.textContent = "·";
    frag.appendChild(sep);
  } else {
    const sep0 = document.createElement("span");
    sep0.className = "forum-bc-sep";
    sep0.setAttribute("aria-hidden", "true");
    sep0.textContent = "·";
    frag.appendChild(sep0);
  }

  type BcSeg = { label: string; action?: () => void };
  const segs: BcSeg[] = [{ label: "Forum", action: panel !== "home" ? renderForumHome : undefined }];

  if (panel === "category" && currentForumCategory) {
    segs.push({ label: currentForumCategory.name });
  } else if (panel === "board" && currentForumCategory && currentForumBoard) {
    segs.push({ label: currentForumCategory.name, action: () => renderForumCategory(currentForumCategory!) });
    segs.push({ label: currentForumBoard.name });
  } else if (panel === "thread" && currentForumCategory && currentForumBoard) {
    segs.push({ label: currentForumCategory.name, action: () => renderForumCategory(currentForumCategory!) });
    segs.push({ label: currentForumBoard.name, action: () => { void loadForumBoard(currentForumBoard!.id); } });
    if (currentForumThread) segs.push({ label: `#${currentForumThread.id}` });
  }

  segs.forEach((seg, i) => {
    const el = seg.action ? document.createElement("button") : document.createElement("span");
    el.className = "forum-bc-seg" + (i === segs.length - 1 ? " forum-bc-seg--current" : "");
    if (seg.action && el instanceof HTMLButtonElement) {
      el.type = "button";
      el.addEventListener("click", seg.action);
    }
    el.textContent = seg.label;
    frag.appendChild(el);
    if (i < segs.length - 1) {
      const arrow = document.createElement("span");
      arrow.className = "forum-bc-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = "›";
      frag.appendChild(arrow);
    }
  });

  elements.forumBreadcrumb.replaceChildren(frag);
}

// ── Home (Slices 3 + 4) ───────────────────────────────────────────────

function renderForumHome(): void {
  currentForumCategory = null;
  currentForumBoard = null;
  currentForumThread = null;
  showForumPanel("home");

  elements.forumEyebrow.textContent = "Student";
  elements.forumPageTitle.textContent = "Forum";
  elements.forumPageTitle.style.color = "";

  const cats = forumCategories();
  if (cats.length === 0) {
    const empty = document.createElement("p");
    empty.className = "forum-empty";
    empty.textContent = "No subjects yet.";
    elements.forumHome.replaceChildren(empty);
    return;
  }
  const grid = document.createElement("div");
  grid.className = "forum-home-grid";
  cats.forEach((cat, i) => {
    const card = createForumCategoryCard(cat);
    card.style.animationDelay = `${i * 40}ms`;
    grid.appendChild(card);
  });
  elements.forumHome.replaceChildren(grid);
}

function createForumCategoryCard(cat: ForumCategoryWithBoards): HTMLElement {
  const accent = cat.color || forumAccent(cat.id);
  const card = document.createElement("article");
  card.className = "forum-ccard";
  card.style.setProperty("--forum-accent", accent);
  card.setAttribute("tabindex", "0");
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Browse ${cat.name}`);

  card.addEventListener("click", (e) => {
    if ((e.target as HTMLElement).closest(".forum-active-board-row")) return;
    renderForumCategory(cat);
  });
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); renderForumCategory(cat); }
  });

  const bar = document.createElement("div");
  bar.className = "forum-ccard-bar";

  const inner = document.createElement("div");
  inner.className = "forum-ccard-inner";

  // Head: folder icon + name + count
  const head = document.createElement("div");
  head.className = "forum-ccard-head";
  const headLeft = document.createElement("div");
  headLeft.className = "forum-ccard-head-left";
  headLeft.innerHTML = `<svg class="forum-ccard-ico" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
  const nameEl = document.createElement("h2");
  nameEl.className = "forum-ccard-name";
  nameEl.textContent = cat.name;
  headLeft.appendChild(nameEl);
  const headRight = document.createElement("span");
  headRight.className = "forum-ccard-count";
  const totalPosts = cat.boards.reduce((n, b) => n + b.thread_count, 0);
  headRight.textContent = `${cat.boards.length} boards · ${totalPosts} posts`;
  head.append(headLeft, headRight);
  inner.appendChild(head);

  // Blurb
  if (cat.description) {
    const blurb = document.createElement("p");
    blurb.className = "forum-ccard-blurb";
    blurb.textContent = cat.description;
    inner.appendChild(blurb);
  }

  // Recently active boards (slice 4)
  const activeBoards = [...cat.boards]
    .filter((b) => b.latest_activity_at)
    .sort((a, b) => new Date(b.latest_activity_at!).getTime() - new Date(a.latest_activity_at!).getTime())
    .slice(0, 2);

  if (activeBoards.length > 0) {
    const activeSection = document.createElement("div");
    activeSection.className = "forum-ccard-active";
    const lbl = document.createElement("span");
    lbl.className = "forum-ccard-active-label";
    lbl.textContent = "Recently active";
    activeSection.appendChild(lbl);
    activeBoards.forEach((board) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "forum-active-board-row";
      row.addEventListener("click", (e) => {
        e.stopPropagation();
        currentForumCategory = cat;
        void loadForumBoard(board.id);
      });
      const dot = document.createElement("span");
      dot.className = "forum-unread-dot forum-unread-dot--off";
      dot.setAttribute("aria-hidden", "true");
      const name = document.createElement("span");
      name.className = "forum-active-board-name";
      name.textContent = board.name;
      const meta = document.createElement("span");
      meta.className = "forum-active-board-meta";
      meta.textContent = `💬 ${board.thread_count} · ${board.latest_activity_at ? formatTimestamp(board.latest_activity_at) : "—"}`;
      row.append(dot, name, meta);
      activeSection.appendChild(row);
    });
    inner.appendChild(activeSection);
  }

  // Footer
  const foot = document.createElement("div");
  foot.className = "forum-ccard-foot";
  foot.textContent = `Browse ${cat.boards.length} ${cat.boards.length === 1 ? "board" : "boards"} →`;
  inner.appendChild(foot);

  card.append(bar, inner);
  return card;
}

// ── Category view (Slices 5 + 6) ──────────────────────────────────────

function renderForumCategory(cat: ForumCategoryWithBoards): void {
  currentForumCategory = cat;
  currentForumBoard = null;
  currentForumThread = null;
  showForumPanel("category");

  const accent = cat.color || forumAccent(cat.id);
  elements.forumEyebrow.textContent = "Subject";
  elements.forumPageTitle.textContent = cat.name;
  elements.forumPageTitle.style.color = accent;

  const container = elements.forumCategoryView;
  container.replaceChildren();
  container.style.setProperty("--forum-accent", accent);

  const totalPosts = cat.boards.reduce((n, b) => n + b.thread_count, 0);
  const totalReplies = cat.boards.reduce((n, b) => n + b.reply_count, 0);

  const sectionHead = document.createElement("div");
  sectionHead.className = "forum-section-head";
  if (cat.description) {
    const blurb = document.createElement("p");
    blurb.className = "forum-section-blurb";
    blurb.textContent = cat.description;
    sectionHead.appendChild(blurb);
  }
  const stats = document.createElement("div");
  stats.className = "forum-section-stats";
  stats.innerHTML = `<span>${cat.boards.length} boards</span><span aria-hidden="true">·</span><span>${totalPosts} posts</span><span aria-hidden="true">·</span><span>${totalReplies} replies</span>`;
  sectionHead.appendChild(stats);
  container.appendChild(sectionHead);

  // Toolbar + inline create form (slice 6)
  const toolbar = document.createElement("div");
  toolbar.className = "forum-toolbar";

  const newBoardBtn = document.createElement("button");
  newBoardBtn.type = "button";
  newBoardBtn.className = "button primary";
  newBoardBtn.textContent = "+ New sub-board";

  const searchInput = document.createElement("input");
  searchInput.type = "search";
  searchInput.className = "forum-search";
  searchInput.placeholder = "Search boards…";
  toolbar.append(newBoardBtn, searchInput);
  container.appendChild(toolbar);

  // Inline form
  const createForm = document.createElement("form");
  createForm.className = "forum-create-form hidden";
  createForm.style.setProperty("--forum-accent", accent);
  createForm.innerHTML = `
    <label class="field"><span>Name</span><input id="forum-create-board-name" maxlength="90" required placeholder="e.g. Algorithm Questions" /></label>
    <label class="field"><span>Description</span><textarea id="forum-create-board-desc" rows="2" maxlength="360" placeholder="What belongs here?"></textarea></label>
    <div class="actions"><button type="submit" class="button primary" id="forum-create-board-submit">Create</button><button type="button" class="button secondary" id="forum-create-board-cancel">Cancel</button></div>
  `;
  container.appendChild(createForm);

  newBoardBtn.addEventListener("click", () => {
    createForm.classList.toggle("hidden");
    if (!createForm.classList.contains("hidden")) {
      (createForm.querySelector("#forum-create-board-name") as HTMLInputElement)?.focus();
    }
  });
  createForm.querySelector("#forum-create-board-cancel")?.addEventListener("click", () => {
    createForm.classList.add("hidden");
  });
  createForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void handleCreateForumBoardInline(cat.id, createForm);
  });

  // Board grid
  if (cat.boards.length === 0) {
    const empty = document.createElement("p");
    empty.className = "forum-empty";
    empty.textContent = "No sub-boards yet. Create one above.";
    container.appendChild(empty);
    return;
  }

  const grid = document.createElement("div");
  grid.className = "forum-board-grid";
  const allBoardEls: HTMLElement[] = [];
  cat.boards.forEach((board, i) => {
    const card = createForumBoardCard(board, cat);
    card.style.animationDelay = `${i * 40}ms`;
    allBoardEls.push(card);
    grid.appendChild(card);
  });
  container.appendChild(grid);

  searchInput.addEventListener("input", () => {
    const q = searchInput.value.toLowerCase();
    allBoardEls.forEach((el) => {
      const name = el.querySelector(".forum-board-card-name")?.textContent?.toLowerCase() ?? "";
      el.classList.toggle("hidden", q !== "" && !name.includes(q));
    });
  });
}

function createForumBoardCard(board: ForumBoard, cat: ForumCategoryWithBoards): HTMLElement {
  const accent = cat.color || forumAccent(cat.id);
  const card = document.createElement("article");
  card.className = "forum-board-card";
  card.setAttribute("tabindex", "0");
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Open ${board.name}`);
  card.style.setProperty("--forum-accent", accent);

  card.addEventListener("click", () => {
    currentForumCategory = cat;
    void loadForumBoard(board.id);
  });
  card.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      currentForumCategory = cat;
      void loadForumBoard(board.id);
    }
  });

  const bar = document.createElement("div");
  bar.className = "forum-board-card-bar";

  const inner = document.createElement("div");
  inner.className = "forum-board-card-inner";

  const name = document.createElement("span");
  name.className = "forum-board-card-name";
  name.textContent = board.name;

  const desc = document.createElement("p");
  desc.className = "forum-board-card-desc";
  desc.textContent = board.description;

  const foot = document.createElement("div");
  foot.className = "forum-board-card-foot";
  const meta = document.createElement("span");
  meta.className = "forum-card-meta";
  meta.textContent = `💬 ${board.thread_count} posts · ${board.reply_count} replies · ${board.latest_activity_at ? formatTimestamp(board.latest_activity_at) : "No activity yet"}`;
  const arrow = document.createElement("span");
  arrow.className = "forum-board-card-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";
  foot.append(meta, arrow);

  inner.append(name, desc, foot);
  card.append(bar, inner);
  return card;
}

async function handleCreateForumBoardInline(categoryId: number, form: HTMLFormElement): Promise<void> {
  const nameInput = form.querySelector<HTMLInputElement>("#forum-create-board-name");
  const descInput = form.querySelector<HTMLTextAreaElement>("#forum-create-board-desc");
  const submitBtn = form.querySelector<HTMLButtonElement>("#forum-create-board-submit");
  const name = nameInput?.value.trim() ?? "";
  const description = descInput?.value.trim() ?? "";
  if (!name) return;
  if (submitBtn) submitBtn.disabled = true;
  try {
    const board = await api.createForumBoard({ category_id: categoryId, name, description });
    forumData = await api.forumIndex();
    const updatedCat = forumCategories().find((c) => c.id === categoryId);
    if (updatedCat) {
      renderForumCategory(updatedCat);
      showToast(`Sub-board '${board.name}' created`);
    }
  } catch (error) {
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Failed", "error");
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// ── Board view (Slices 7 + 8) ──────────────────────────────────────────

async function loadForumBoard(boardId: number): Promise<void> {
  setStatus(elements.forumStatus, "Loading");
  try {
    const resp = await api.forumBoard(boardId);
    renderForumBoard(resp.board, resp.threads);
    setStatus(elements.forumStatus, "");
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Board failed", "error");
  }
}

function renderForumBoard(board: ForumBoard, threads: ForumThreadSummary[]): void {
  currentForumBoard = board;
  currentForumThread = null;
  if (!currentForumCategory) {
    currentForumCategory = forumCategories().find((c) => c.id === board.category_id) ?? null;
  }
  showForumPanel("board");

  const accent = currentForumCategory?.color || forumAccent(board.category_id);
  elements.forumEyebrow.textContent = currentForumCategory?.name ?? "";
  elements.forumPageTitle.textContent = board.name;
  elements.forumPageTitle.style.color = "";

  const container = elements.forumBoardView;
  container.replaceChildren();
  container.style.setProperty("--forum-accent", accent);

  const sectionHead = document.createElement("div");
  sectionHead.className = "forum-section-head";
  if (board.description) {
    const blurb = document.createElement("p");
    blurb.className = "forum-section-blurb";
    blurb.textContent = board.description;
    sectionHead.appendChild(blurb);
  }
  const stats = document.createElement("div");
  stats.className = "forum-section-stats";
  stats.innerHTML = `<span>${board.thread_count} posts</span><span aria-hidden="true">·</span><span>${board.reply_count} replies</span>`;
  sectionHead.appendChild(stats);
  container.appendChild(sectionHead);

  // Toolbar
  const toolbar = document.createElement("div");
  toolbar.className = "forum-toolbar";
  const newPostBtn = document.createElement("button");
  newPostBtn.type = "button";
  newPostBtn.className = "button primary";
  newPostBtn.textContent = "+ New post";
  newPostBtn.addEventListener("click", () => openForumCompose(board.id));

  const filterSeg = document.createElement("div");
  filterSeg.className = "segmented forum-filter-seg";
  filterSeg.setAttribute("role", "tablist");
  ["All", "Unread"].forEach((lbl, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `segment${i === 0 ? " active" : ""}`;
    btn.textContent = lbl;
    btn.setAttribute("role", "tab");
    filterSeg.appendChild(btn);
  });

  const searchInput = document.createElement("input");
  searchInput.type = "search";
  searchInput.className = "forum-search";
  searchInput.placeholder = "Search posts…";
  toolbar.append(newPostBtn, filterSeg, searchInput);
  container.appendChild(toolbar);

  if (threads.length === 0) {
    const empty = document.createElement("p");
    empty.className = "forum-empty";
    empty.textContent = "No posts yet. Be the first!";
    container.appendChild(empty);
    return;
  }

  // Time grouping (slice 7)
  const now = Date.now();
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  const thisWeek = threads.filter((t) => now - new Date(t.latest_activity_at).getTime() < weekMs);
  const earlier = threads.filter((t) => now - new Date(t.latest_activity_at).getTime() >= weekMs);

  const list = document.createElement("div");
  list.className = "forum-thread-list";

  function addGroup(label: string, emoji: string, groupThreads: ForumThreadSummary[]): void {
    if (groupThreads.length === 0) return;
    const lbl = document.createElement("div");
    lbl.className = "forum-group-label";
    lbl.textContent = `${emoji} ${label}`.trim();
    list.appendChild(lbl);
    groupThreads.forEach((t) => list.appendChild(createForumThreadRow(t)));
  }
  addGroup("This week", "", thisWeek);
  addGroup("Earlier", "", earlier);
  container.appendChild(list);

  const allRows = Array.from(list.querySelectorAll<HTMLElement>(".forum-thread-row"));
  searchInput.addEventListener("input", () => {
    const q = searchInput.value.toLowerCase();
    allRows.forEach((row) => {
      const title = row.querySelector(".forum-thread-row-title")?.textContent?.toLowerCase() ?? "";
      row.classList.toggle("hidden", q !== "" && !title.includes(q));
    });
  });
}

function createForumThreadRow(thread: ForumThreadSummary): HTMLElement {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "forum-thread-row";
  row.addEventListener("click", () => { void loadForumThread(thread.id); });

  const isRead = localStorage.getItem(`studylens.forum.read.${thread.id}`) !== null;
  const dot = document.createElement("span");
  dot.className = "forum-unread-dot" + (isRead ? " forum-unread-dot--off" : "");
  dot.setAttribute("aria-label", isRead ? "" : "Unread");

  const content = document.createElement("div");
  content.className = "forum-thread-row-content";

  const titleLine = document.createElement("div");
  titleLine.className = "forum-thread-row-titleline";
  const titleEl = document.createElement("span");
  titleEl.className = "forum-thread-row-title";
  titleEl.textContent = thread.title;
  titleLine.appendChild(titleEl);
  if (thread.dylen_replied) {
    const chip = document.createElement("span");
    chip.className = "forum-chip forum-chip--bot";
    chip.textContent = "dylen";
    titleLine.appendChild(chip);
  }

  const metaLine = document.createElement("div");
  metaLine.className = "forum-thread-row-meta";
  const isStaff = thread.author_role === "admin";
  let authorHtml = thread.is_anonymous ? "🎭 Anonymous" : thread.author_username;
  if (isStaff && !thread.is_anonymous) authorHtml += ` <span class="forum-chip forum-chip--staff">STAFF</span>`;
  metaLine.innerHTML = `${authorHtml} <span class="forum-mono">· ${formatTimestamp(thread.latest_activity_at)} · 💬 ${thread.reply_count}</span>`;
  if (thread.course_id) {
    const pill = document.createElement("span");
    pill.className = "course-code forum-course-pill";
    pill.textContent = thread.course_id;
    metaLine.appendChild(pill);
  }

  const chevron = document.createElement("span");
  chevron.className = "forum-thread-row-chevron";
  chevron.setAttribute("aria-hidden", "true");
  chevron.textContent = "›";

  content.append(titleLine, metaLine);
  row.append(dot, content, chevron);
  return row;
}

// ── Thread view (Slices 9–12) ──────────────────────────────────────────

async function loadForumThread(threadId: number): Promise<void> {
  setStatus(elements.forumStatus, "Loading post");
  try {
    const thread = await api.forumThread(threadId);
    renderForumThread(thread);
    localStorage.setItem(`studylens.forum.read.${threadId}`, "1");
    setStatus(elements.forumStatus, "");
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Post failed", "error");
  }
}

function renderForumThread(thread: ForumThread): void {
  currentForumThread = thread;
  if (!currentForumBoard) currentForumBoard = findForumBoard(thread.board_id);
  if (!currentForumCategory) {
    currentForumCategory = forumCategories().find((c) => c.id === thread.category_id) ?? null;
  }
  showForumPanel("thread");

  elements.forumEyebrow.textContent = currentForumBoard?.name ?? thread.board_name;
  elements.forumPageTitle.textContent = thread.title;
  elements.forumPageTitle.style.color = "";

  const accent = currentForumCategory?.color || forumAccent(thread.category_id);
  const container = elements.forumThreadView;
  container.replaceChildren();
  container.style.setProperty("--forum-accent", accent);

  // Header: title + #id + tool row (slices 9 + 10)
  const header = document.createElement("div");
  header.className = "forum-thread-header";

  const titleRow = document.createElement("div");
  titleRow.className = "forum-thread-titlerow";
  const h2 = document.createElement("h2");
  h2.className = "forum-thread-h2";
  h2.textContent = thread.title;
  const idBadge = document.createElement("span");
  idBadge.className = "forum-thread-id";
  idBadge.textContent = `#${thread.id}`;
  titleRow.append(h2, idBadge);
  header.appendChild(titleRow);

  // Tool row
  const toolRow = document.createElement("div");
  toolRow.className = "forum-thread-toolrow";
  const toolDefs: Array<{ key: string; label: string; svg: string }> = [
    { key: "pin", label: "Pin", svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24z"/></svg>` },
    { key: "star", label: "Star", svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>` },
    { key: "follow", label: "Follow", svg: `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>` },
  ];
  toolDefs.forEach(({ key, label, svg }) => {
    const storageKey = `studylens.forum.${key}.${thread.id}`;
    const isActive = localStorage.getItem(storageKey) !== null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `forum-tool-btn${isActive ? " active" : ""}`;
    btn.innerHTML = `${svg}<span>${label}</span>`;
    btn.addEventListener("click", () => {
      if (localStorage.getItem(storageKey)) { localStorage.removeItem(storageKey); btn.classList.remove("active"); }
      else { localStorage.setItem(storageKey, "1"); btn.classList.add("active"); }
    });
    toolRow.appendChild(btn);
  });
  const viewCount = document.createElement("span");
  viewCount.className = "forum-view-count";
  viewCount.textContent = `${thread.reply_count + 1} views`;
  toolRow.appendChild(viewCount);
  header.appendChild(toolRow);
  container.appendChild(header);

  // OP block (slice 9)
  container.appendChild(createForumPostBlock(thread.id, thread.is_anonymous ? "🎭 Anonymous" : thread.author_username, thread.author_role, thread.body, thread.created_at, [], thread.board_name, thread.course_id ?? null, true, thread.is_anonymous));

  // Replies (slice 11)
  if (thread.replies.length > 0) {
    const divider = document.createElement("div");
    divider.className = "forum-replies-divider";
    divider.textContent = `${thread.replies.length} ${thread.replies.length === 1 ? "reply" : "replies"}`;
    container.appendChild(divider);
    thread.replies.forEach((reply) => container.appendChild(createForumReplyBlock(reply)));
  } else {
    const empty = document.createElement("p");
    empty.className = "forum-empty";
    empty.textContent = "No replies yet.";
    container.appendChild(empty);
  }

  // Reply composer (slice 12)
  container.appendChild(createReplyComposer(thread.id));
}

function createForumPostBlock(
  id: number,
  authorUsername: string,
  authorRole: string,
  body: string,
  createdAt: string,
  citations: Citation[],
  boardName: string,
  courseId: string | null,
  isOP: boolean,
  isAnonymous = false
): HTMLElement {
  const article = document.createElement("article");
  article.className = `forum-post${isOP ? " forum-post--op" : ""}`;

  // Like rail
  const likeRail = document.createElement("div");
  likeRail.className = "forum-like-rail";
  const likeKey = `studylens.forum.like.${isOP ? "op" : "r"}.${id}`;
  let liked = localStorage.getItem(likeKey) !== null;
  const likeBtn = document.createElement("button");
  likeBtn.type = "button";
  likeBtn.className = `forum-like-btn${liked ? " active" : ""}`;
  likeBtn.setAttribute("aria-label", "Like");
  likeBtn.innerHTML = `<svg width="15" height="15" viewBox="0 0 24 24" fill="${liked ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>`;
  likeBtn.addEventListener("click", () => {
    liked = !liked;
    likeBtn.classList.toggle("active", liked);
    const svg = likeBtn.querySelector("svg");
    if (svg) svg.setAttribute("fill", liked ? "currentColor" : "none");
    if (liked) localStorage.setItem(likeKey, "1"); else localStorage.removeItem(likeKey);
  });
  likeRail.appendChild(likeBtn);

  // Post body
  const postBody = document.createElement("div");
  postBody.className = "forum-post-body";

  const authorHead = document.createElement("div");
  authorHead.className = "forum-post-authorhead";
  const avatar = document.createElement("div");
  avatar.className = "forum-avatar";
  avatar.textContent = isAnonymous ? "🎭" : authorUsername.charAt(0).toUpperCase();
  const authorInfo = document.createElement("div");
  authorInfo.className = "forum-post-authorinfo";
  const authorName = document.createElement("span");
  authorName.className = "forum-post-authorname";
  authorName.textContent = authorUsername;
  const authorMeta = document.createElement("span");
  authorMeta.className = "forum-post-authormeta";
  authorMeta.innerHTML = `${formatTimestamp(createdAt)} <span class="forum-post-in">in ${boardName}</span>`;
  authorInfo.append(authorName, authorMeta);

  const authorRight = document.createElement("div");
  authorRight.className = "forum-post-authorright";
  if (authorRole !== "student") {
    const chip = document.createElement("span");
    chip.className = `forum-chip forum-chip--${authorRole === "bot" ? "bot" : "staff"}`;
    chip.textContent = authorRole === "bot" ? "dylen" : "STAFF";
    authorRight.appendChild(chip);
  }
  if (courseId) {
    const pill = document.createElement("span");
    pill.className = "course-code forum-course-pill";
    pill.textContent = courseId;
    authorRight.appendChild(pill);
  }

  authorHead.append(avatar, authorInfo, authorRight);
  postBody.appendChild(authorHead);

  const textEl = document.createElement("div");
  textEl.className = "forum-post-text";
  textEl.appendChild(highlightMentions(body));
  postBody.appendChild(textEl);

  if (citations.length > 0) {
    const cites = document.createElement("div");
    cites.className = "forum-citations";
    citations.forEach((c, i) => {
      const chip = document.createElement("a");
      chip.className = "chat-citation-chip";
      chip.textContent = citationLabel(c, i);
      const url = buildCitationUrl(c);
      if (url) { chip.href = url; chip.target = "_blank"; chip.rel = "noopener noreferrer"; }
      cites.appendChild(chip);
    });
    postBody.appendChild(cites);
  }

  article.append(likeRail, postBody);
  return article;
}

function createForumReplyBlock(reply: ForumReply): HTMLElement {
  const isBot = reply.author_role === "bot";
  const article = document.createElement("article");
  article.className = `forum-reply${isBot ? " forum-reply--bot" : ""}`;

  const avatar = document.createElement("div");
  avatar.className = `forum-avatar${isBot ? " forum-avatar--bot" : ""}`;
  if (isBot) {
    avatar.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.3L12 17l-6.2 4.2 2.4-7.3L2 9.4h7.6z"/></svg>`;
  } else if (reply.is_anonymous) {
    avatar.textContent = "🎭";
  } else {
    avatar.textContent = reply.author_username.charAt(0).toUpperCase();
  }

  const content = document.createElement("div");
  content.className = "forum-reply-content";

  const head = document.createElement("div");
  head.className = "forum-reply-head";
  const author = document.createElement("span");
  author.className = "forum-reply-author";
  author.textContent = reply.is_anonymous ? "🎭 Anonymous" : reply.author_username;
  head.appendChild(author);
  if (reply.author_role !== "student") {
    const chip = document.createElement("span");
    chip.className = `forum-chip forum-chip--${isBot ? "bot" : "staff"}`;
    chip.textContent = isBot ? "dylen" : "STAFF";
    head.appendChild(chip);
  }
  const time = document.createElement("span");
  time.className = "forum-reply-time";
  time.textContent = formatTimestamp(reply.created_at);
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "forum-reply-copy";
  copyBtn.setAttribute("aria-label", "Copy reply");
  copyBtn.innerHTML = COPY_SVG;
  copyBtn.addEventListener("click", () => {
    void navigator.clipboard.writeText(reply.body).then(() => {
      copyBtn.innerHTML = CHECK_SVG;
      setTimeout(() => { copyBtn.innerHTML = COPY_SVG; }, 1200);
    });
  });
  head.append(time, copyBtn);

  const bodyEl = document.createElement("div");
  bodyEl.className = "forum-reply-body";
  if (isBot) { bodyEl.innerHTML = renderAnswer(reply.body); applyMentionHighlight(bodyEl); }
  else { bodyEl.appendChild(highlightMentions(reply.body)); }

  content.append(head, bodyEl);

  if (reply.citations.length > 0) {
    const cites = document.createElement("div");
    cites.className = "forum-citations";
    reply.citations.forEach((c, i) => {
      const chip = document.createElement("a");
      chip.className = "chat-citation-chip";
      chip.textContent = citationLabel(c, i);
      const url = buildCitationUrl(c);
      if (url) { chip.href = url; chip.target = "_blank"; chip.rel = "noopener noreferrer"; }
      cites.appendChild(chip);
    });
    content.appendChild(cites);
  }

  article.append(avatar, content);
  return article;
}

function createReplyComposer(threadId: number): HTMLElement {
  const composer = document.createElement("div");
  composer.className = "forum-reply-composer";

  const hint = document.createElement("p");
  hint.className = "forum-composer-hint";
  hint.textContent = "@dylen answers from your course materials";

  const editor = document.createElement("div");
  editor.className = "forum-mention-editor";
  editor.setAttribute("contenteditable", "true");
  editor.setAttribute("role", "textbox");
  editor.setAttribute("aria-multiline", "true");
  editor.dataset.placeholder = "Add your reply… Mention @dylen to ask the course assistant";

  const actions = document.createElement("div");
  actions.className = "actions";
  const submitBtn = document.createElement("button");
  submitBtn.type = "button";
  submitBtn.className = "button primary";
  submitBtn.textContent = "Reply";
  submitBtn.disabled = true;
  setupMentionEditor(editor, () => {
    submitBtn.disabled = !getEditorText(editor).trim();
  });
  const anonLabel = document.createElement("label");
  anonLabel.className = "forum-reply-anon";
  const anonCheck = document.createElement("input");
  anonCheck.type = "checkbox";
  anonCheck.className = "forum-toggle-input";
  anonLabel.append(anonCheck, document.createTextNode(" Post anonymously"));

  submitBtn.addEventListener("click", async () => {
    await handleCreateForumReply(threadId, editor, submitBtn, anonCheck.checked);
    anonCheck.checked = false;
  });
  actions.appendChild(submitBtn);
  composer.append(hint, editor, anonLabel, actions);
  return composer;
}

async function handleCreateForumReply(
  threadId: number,
  editor: HTMLElement,
  submitBtn: HTMLButtonElement,
  anonymous = false
): Promise<void> {
  const body = getEditorText(editor).trim();
  if (!body) return;
  submitBtn.disabled = true;
  submitBtn.textContent = "Posting…";
  try {
    const thread = await api.createForumReply(threadId, { body, anonymous });
    editor.innerHTML = "";
    forumData = await api.forumIndex();
    renderForumThread(thread);
    showToast(thread.dylen_replied ? "Posted · dylen replied" : "Reply posted");
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Reply failed", "error");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Reply";
  }
}

// ── Compose (Slices 13 + 14) ───────────────────────────────────────────

function openForumCompose(targetBoardId?: number): void {
  forumComposeTargetBoardId = targetBoardId ?? null;
  const cats = forumCategories();
  elements.forumComposeCategorySel.replaceChildren(
    ...cats.map((cat) => {
      const opt = document.createElement("option");
      opt.value = String(cat.id);
      opt.textContent = cat.name;
      return opt;
    })
  );
  if (targetBoardId) {
    const board = allForumBoards().find((b) => b.id === targetBoardId);
    if (board) elements.forumComposeCategorySel.value = String(board.category_id);
  } else if (currentForumCategory) {
    elements.forumComposeCategorySel.value = String(currentForumCategory.id);
  }
  resetBoardPicker();
  if (targetBoardId) {
    const board = allForumBoards().find((b) => b.id === targetBoardId);
    if (board) selectBoardInPicker(board);
  }
  elements.forumComposeTitle.value = "";
  elements.forumComposeBody.innerHTML = "";

  elements.forumComposeAnon.checked = false;
  updateForumComposeSubmitState();
  elements.forumCompose.classList.remove("hidden");
  elements.forumComposeTitle.focus();
}

function closeForumCompose(): void {
  elements.forumCompose.classList.add("hidden");
}

function getPickerCategoryBoards(): ForumBoard[] {
  const catId = Number(elements.forumComposeCategorySel.value);
  return forumCategories().find((c) => c.id === catId)?.boards ?? [];
}

function renderBoardPickerMenu(): void {
  const q = elements.forumBoardPickerInput.value.toLowerCase();
  const boards = getPickerCategoryBoards();
  const filtered = q ? boards.filter((b) => b.name.toLowerCase().includes(q)) : boards;
  const menu = elements.forumBoardPickerMenu;

  if (filtered.length === 0) {
    const catName = forumCategories().find((c) => c.id === Number(elements.forumComposeCategorySel.value))?.name ?? "this category";
    const li = document.createElement("li");
    li.className = "forum-board-picker-empty";
    li.textContent = q ? `No board matches "${q}" in ${catName}.` : "No boards in this category yet.";
    menu.replaceChildren(li);
  } else {
    menu.replaceChildren(
      ...filtered.map((board) => {
        const li = document.createElement("li");
        li.className = "forum-board-picker-item";
        li.setAttribute("role", "option");
        if (board.id === forumComposeSelectedBoard?.id) li.classList.add("selected");

        const nameEl = document.createElement("span");
        nameEl.className = "forum-board-picker-name";
        if (q) {
          const idx = board.name.toLowerCase().indexOf(q);
          if (idx >= 0) {
            nameEl.appendChild(document.createTextNode(board.name.slice(0, idx)));
            const mark = document.createElement("mark");
            mark.className = "forum-board-picker-match";
            mark.textContent = board.name.slice(idx, idx + q.length);
            nameEl.appendChild(mark);
            nameEl.appendChild(document.createTextNode(board.name.slice(idx + q.length)));
          } else {
            nameEl.textContent = board.name;
          }
        } else {
          nameEl.textContent = board.name;
        }

        const countEl = document.createElement("span");
        countEl.className = "forum-board-picker-count";
        countEl.textContent = `${board.thread_count} posts`;

        li.append(nameEl, countEl);
        li.addEventListener("mousedown", (e) => {
          e.preventDefault(); // keep focus on input
          selectBoardInPicker(board);
        });
        return li;
      })
    );
  }
  menu.classList.remove("hidden");
}

function selectBoardInPicker(board: ForumBoard): void {
  forumComposeSelectedBoard = { id: board.id, name: board.name };
  elements.forumBoardPickerInput.value = board.name;
  elements.forumBoardPickerMenu.classList.add("hidden");
  updateForumComposeSubmitState();
}

function resetBoardPicker(): void {
  forumComposeSelectedBoard = null;
  elements.forumBoardPickerInput.value = "";
  elements.forumBoardPickerMenu.classList.add("hidden");
  clearBoardPickerError();
}

function validateBoardPicker(): void {
  const q = elements.forumBoardPickerInput.value.trim();
  if (!q || forumComposeSelectedBoard) { clearBoardPickerError(); return; }
  const catName = forumCategories().find(
    (c) => c.id === Number(elements.forumComposeCategorySel.value)
  )?.name ?? "this category";
  elements.forumBoardPickerInput.closest<HTMLElement>(".forum-board-picker-wrap")?.classList.add("invalid");
  elements.forumBoardPickerError.textContent =
    `"${q}" isn't a board in ${catName}. Pick an existing board, or create it from the ${catName} page first.`;
  elements.forumBoardPickerError.classList.remove("hidden");
}

function clearBoardPickerError(): void {
  elements.forumBoardPickerInput.closest<HTMLElement>(".forum-board-picker-wrap")?.classList.remove("invalid");
  elements.forumBoardPickerError.classList.add("hidden");
}

function updateForumComposeSubmitState(): void {
  const ready = !!elements.forumComposeTitle.value.trim()
    && !!getEditorText(elements.forumComposeBody).trim()
    && !!forumComposeSelectedBoard;
  elements.forumComposeSubmit.disabled = !ready;
  elements.forumComposeSubmit2.disabled = !ready;
}

async function handleCreateForumThread(): Promise<void> {
  const boardId = forumComposeSelectedBoard?.id;
  const title = elements.forumComposeTitle.value.trim();
  const body = getEditorText(elements.forumComposeBody).trim();
  if (!boardId || !title || !body) return;

  elements.forumComposeSubmit.disabled = true;
  elements.forumComposeSubmit.textContent = "Posting…";
  elements.forumComposeSubmit2.disabled = true;
  elements.forumComposeSubmit2.textContent = "Posting…";
  try {
    const thread = await api.createForumThread({ board_id: boardId, title, body, course_id: null, anonymous: elements.forumComposeAnon.checked });
    forumData = await api.forumIndex();
    closeForumCompose();
    const board = allForumBoards().find((b) => b.id === boardId);
    if (board) currentForumCategory = forumCategories().find((c) => c.id === board.category_id) ?? null;
    renderForumThread(thread);
    showToast(thread.dylen_replied ? "Posted · dylen replied" : `Posted '${title}'`);
  } catch (error) {
    if (handleAuthRequired(error)) return;
    setStatus(elements.forumStatus, error instanceof Error ? error.message : "Post failed", "error");
  } finally {
    elements.forumComposeSubmit.disabled = false;
    elements.forumComposeSubmit.textContent = "Post";
    elements.forumComposeSubmit2.disabled = false;
    elements.forumComposeSubmit2.textContent = "Post";
    updateForumComposeSubmitState();
  }
}

// ── Forum helpers ──────────────────────────────────────────────────────

// ── contenteditable mention editor ─────────────────────────────────────

function getEditorText(el: HTMLElement): string {
  let text = "";
  function walk(node: Node): void {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? "";
    } else if ((node as Element).tagName === "BR") {
      text += "\n";
    } else {
      for (const child of node.childNodes) walk(child);
    }
  }
  for (const child of el.childNodes) walk(child);
  return text;
}

function getCaretOffset(el: HTMLElement): number {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return 0;
  const { focusNode, focusOffset } = sel;
  if (!focusNode) return 0;
  let count = 0;

  function sumAll(node: Node): void {
    if (node.nodeType === Node.TEXT_NODE) count += (node.textContent ?? "").length;
    else if ((node as Element).tagName === "BR") count += 1;
    else for (const c of node.childNodes) sumAll(c);
  }

  function walk(node: Node): boolean {
    if (node === focusNode) {
      if (node.nodeType === Node.TEXT_NODE) {
        count += focusOffset; // char offset within text node
      } else {
        // focusOffset is a child index — sum the first focusOffset children
        let i = 0;
        for (const c of node.childNodes) {
          if (i++ >= focusOffset) break;
          sumAll(c);
        }
      }
      return true;
    }
    if (node.nodeType === Node.TEXT_NODE) count += (node.textContent ?? "").length;
    else if ((node as Element).tagName === "BR") count += 1;
    else for (const c of node.childNodes) { if (walk(c)) return true; }
    return false;
  }
  walk(el);
  return count;
}

function setCaretOffset(el: HTMLElement, target: number): void {
  let rem = target;
  function walk(node: Node): [Node, number] | null {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = (node.textContent ?? "").length;
      if (rem <= len) return [node, rem];
      rem -= len;
    } else if ((node as Element).tagName === "BR") {
      if (rem === 0) {
        const idx = [...(node.parentNode!.childNodes)].indexOf(node as ChildNode);
        return [node.parentNode!, idx];
      }
      rem -= 1;
    } else {
      for (const c of node.childNodes) { const r = walk(c); if (r) return r; }
    }
    return null;
  }
  const result = walk(el) ?? [el, el.childNodes.length];
  const range = document.createRange();
  try { range.setStart(result[0], result[1]); } catch { range.selectNodeContents(el); range.collapse(false); }
  range.collapse(true);
  const sel = window.getSelection()!;
  sel.removeAllRanges();
  sel.addRange(range);
}

function buildEditorFrag(text: string): DocumentFragment {
  const re = /(^|[^\w])(@dylen)\b/gi;
  const frag = document.createDocumentFragment();
  let last = 0;
  let m: RegExpExecArray | null;

  function appendText(s: string): void {
    const lines = s.split("\n");
    lines.forEach((line, i) => {
      if (line) frag.appendChild(document.createTextNode(line));
      if (i < lines.length - 1) frag.appendChild(document.createElement("br"));
    });
  }

  while ((m = re.exec(text)) !== null) {
    appendText(text.slice(last, m.index + m[1].length));
    const span = document.createElement("span");
    span.className = "fx-mention";
    span.textContent = m[2];
    frag.appendChild(span);
    last = m.index + m[0].length;
  }
  appendText(text.slice(last));
  // Trailing BR so cursor can sit on the last empty line
  if (text.endsWith("\n")) frag.appendChild(document.createElement("br"));
  return frag;
}

function formatEditorMentions(el: HTMLElement): void {
  const text = getEditorText(el);
  const caret = getCaretOffset(el);
  el.replaceChildren(buildEditorFrag(text));
  setCaretOffset(el, caret);
}

function setupMentionEditor(el: HTMLElement, onChange: () => void): void {
  let composing = false;

  el.addEventListener("compositionstart", () => { composing = true; });
  el.addEventListener("compositionend", () => {
    composing = false;
    formatEditorMentions(el);
    onChange();
  });

  el.addEventListener("input", () => {
    if (composing) return;
    formatEditorMentions(el);
    onChange();
  });

  // Enter: work in text-space so a BR can never land inside an fx-mention span
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const sel = window.getSelection()!;
      if (!sel.rangeCount) return;
      // Collapse any selection first (mirrors what deleteContents would do)
      const range = sel.getRangeAt(0);
      if (!range.collapsed) range.deleteContents();
      // Read current text and caret from the (possibly modified) DOM
      const text = getEditorText(el);
      const caret = getCaretOffset(el);
      // Insert \n at caret, then rebuild — mentions re-evaluated automatically
      el.replaceChildren(buildEditorFrag(text.slice(0, caret) + "\n" + text.slice(caret)));
      setCaretOffset(el, caret + 1);
      onChange();
    }
  });

  // Paste: strip HTML, insert plain text
  el.addEventListener("paste", (e) => {
    e.preventDefault();
    const plain = e.clipboardData?.getData("text/plain") ?? "";
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    const frag = buildEditorFrag(plain);
    range.insertNode(frag);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
    formatEditorMentions(el);
    onChange();
  });
}

function highlightMentions(text: string): DocumentFragment {
  const frag = document.createDocumentFragment();
  // Mirror backend rule: @dylen must be at start of string or preceded by a non-word char.
  const re = /(^|[^\w])(@dylen)\b/gi;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const pre = m[1];  // "" at start-of-string, or the non-word char before @
    const men = m[2];  // "@dylen" (original casing)
    frag.appendChild(document.createTextNode(text.slice(last, m.index + pre.length)));
    const span = document.createElement("span");
    span.className = "fx-mention";
    span.textContent = men;
    frag.appendChild(span);
    last = m.index + m[0].length;
  }
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  return frag;
}

function applyMentionHighlight(el: HTMLElement): void {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const hits: Text[] = [];
  let n: Node | null;
  while ((n = walker.nextNode())) {
    if (/@dylen/i.test((n as Text).textContent ?? "")) hits.push(n as Text);
  }
  hits.forEach((tn) => tn.parentNode?.replaceChild(highlightMentions(tn.textContent ?? ""), tn));
}

function forumCategories(): ForumCategoryWithBoards[] {
  return forumData?.categories ?? [];
}

function allForumBoards(): ForumBoard[] {
  return forumCategories().flatMap((cat) => cat.boards);
}

function findForumBoard(boardId: number): ForumBoard | null {
  return allForumBoards().find((b) => b.id === boardId) ?? null;
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
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

  // Show processing state on each target card
  targets.forEach((c) => setCardProcessing(c.code, "processing"));

  const queue = [...targets];
  let completed = 0;

  async function worker(): Promise<void> {
    while (true) {
      const course = queue.shift();
      if (!course) return;
      try {
        const report = await api.autoIndexCourse({
          course_id: course.code,
          course_title: course.title,
        });
        const ts = new Date().toISOString();
        const idx = discoveredCourses.findIndex((c) => c.code === course.code);
        if (idx >= 0) discoveredCourses[idx] = { ...discoveredCourses[idx], indexed_at: ts };
        setCardProcessing(course.code, "done", ts);
      } catch (error) {
        if (handleAuthRequired(error)) { queue.length = 0; return; }
        setCardProcessing(course.code, "checking");
        const indexedCourse = await confirmIndexedCourse(course.code);
        if (indexedCourse?.indexed_at) {
          setCardProcessing(course.code, "done", indexedCourse.indexed_at);
        } else {
          setCardProcessing(course.code, "failed");
        }
      }
      completed += 1;
      setStatus(elements.coursesStatus, `${completed}/${targets.length} done`);
    }
  }

  const workers = Array.from({ length: Math.min(INDEX_CONCURRENCY, targets.length) }, () => worker());
  try {
    await Promise.all(workers);
    setStatus(elements.coursesStatus, `Done · ${completed}/${targets.length}`);
    showToast(`Processed ${completed} course${completed !== 1 ? "s" : ""}`);
    selectedCourseCodes.clear();
    renderCourseList();
  } finally {
    elements.coursesIndex.disabled = selectedCourseCodes.size === 0;
    elements.coursesDiscover.disabled = false;
    elements.coursesSelectAll.disabled = false;
  }
}

function setCardProcessing(code: string, state: "processing" | "checking" | "done" | "failed", ts?: string): void {
  const card = elements.coursesList.querySelector<HTMLElement>(`[data-code="${code}"]`);
  if (!card) return;

  const meta = card.querySelector<HTMLElement>("[data-role='meta']");
  const badge = card.querySelector<HTMLElement>("[data-role='badge']");
  const foot = card.querySelector<HTMLElement>(".ccard-foot");

  if (state === "processing" || state === "checking") {
    if (meta) meta.innerHTML = `<svg class="spin" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v6h-6"/></svg> Processing…`;
    if (badge) { badge.className = "ccard-badge ccard-badge--pending"; badge.textContent = "Processing"; }
  } else if (state === "done" && ts) {
    if (meta) meta.innerHTML = `<span class="ccard-meta-check">✓</span> Processed · ${formatTimestamp(ts)}`;
    if (badge) { badge.className = "ccard-badge ccard-badge--ready"; badge.innerHTML = `<svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M2 6l3 3 5-5"/></svg>Ready`; }
    // Add Enter button if not present
    if (foot && !foot.querySelector(".ccard-enter")) {
      const course = discoveredCourses.find((c) => c.code === code);
      if (course) {
        const enterBtn = document.createElement("button");
        enterBtn.type = "button";
        enterBtn.className = "ccard-enter";
        enterBtn.innerHTML = `Enter <span class="ccard-enter-arrow">→</span>`;
        enterBtn.addEventListener("click", (e) => { e.stopPropagation(); enterCourse(course); });
        foot.append(enterBtn);
      }
    }
    card.classList.remove("ccard--selected");
  } else if (state === "failed") {
    if (meta) meta.innerHTML = `<span style="color:var(--danger)">✗ Failed</span>`;
    if (badge) { badge.className = "ccard-badge ccard-badge--pending"; badge.textContent = "Failed"; }
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
