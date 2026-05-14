import { StudyLensClient } from "./api.js";
import { collectPageContext } from "./pageContext.js";
import { loadSettings } from "./storage.js";
import type { Answer, PageContext } from "./types.js";

const pageTitle = document.querySelector<HTMLParagraphElement>("#page-title");
const courseIdInput = document.querySelector<HTMLInputElement>("#course-id");
const questionInput = document.querySelector<HTMLTextAreaElement>("#question");
const askButton = document.querySelector<HTMLButtonElement>("#ask");
const answerSection = document.querySelector<HTMLElement>("#answer");
const citationsSection = document.querySelector<HTMLElement>("#citations");
const optionsButton = document.querySelector<HTMLButtonElement>("#open-options");

let pageContext: PageContext | null = null;

async function getActivePageContext(): Promise<PageContext> {
  if (typeof chrome === "undefined" || !chrome.tabs) {
    return collectPageContext();
  }
  const tabs = chrome.tabs;
  const [tab] = await tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    return collectPageContext();
  }
  try {
    const response = await tabs.sendMessage(tab.id, { type: "studylens:get-page-context" });
    if (response?.type === "studylens:page-context") {
      return response.payload as PageContext;
    }
  } catch {
    return {
      title: tab.title || "Current page",
      url: tab.url || "",
      selectedText: "",
      visibleText: "",
      inferredCourseId: null,
    };
  }
  return collectPageContext();
}

function renderAnswer(answer: Answer): void {
  if (answerSection) {
    answerSection.textContent = answer.answer;
  }
  if (citationsSection) {
    citationsSection.textContent = answer.citations
      .slice(0, 4)
      .map((citation, index) => `${index + 1}. ${citation.title || citation.resource_id}`)
      .join("\n");
  }
}

function setBusy(isBusy: boolean): void {
  if (askButton) {
    askButton.disabled = isBusy;
    askButton.textContent = isBusy ? "Asking..." : "Ask";
  }
}

async function init(): Promise<void> {
  pageContext = await getActivePageContext();
  if (pageTitle) {
    pageTitle.textContent = pageContext.title;
  }
  if (courseIdInput && pageContext.inferredCourseId) {
    courseIdInput.value = pageContext.inferredCourseId;
  }
}

askButton?.addEventListener("click", async () => {
  const question = questionInput?.value.trim() || "";
  if (!question) {
    if (answerSection) {
      answerSection.textContent = "Enter a question first.";
    }
    return;
  }
  setBusy(true);
  try {
    const settings = await loadSettings();
    const client = new StudyLensClient(settings.backendUrl);
    const answer = await client.ask({
      question,
      course_id: courseIdInput?.value.trim() || pageContext?.inferredCourseId || undefined,
      include_exercises: false,
    });
    renderAnswer(answer);
  } catch (error) {
    if (answerSection) {
      answerSection.textContent = error instanceof Error ? error.message : "StudyLens request failed.";
    }
  } finally {
    setBusy(false);
  }
});

optionsButton?.addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});

void init();
