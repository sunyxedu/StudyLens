import type { PageContext } from "./types.js";

const COURSE_ID_PATTERN = /\b(?:COMP\d{5}|[A-Z]{3,5}\d{4,5}|CO\d{3,5})\b/i;
const VIDEO_HOST_PATTERN = /(?:panopto|youtube\.com|youtu\.be|vimeo)/i;

interface ReadableDocument {
  title: string;
  location: { href: string };
  body?: { innerText?: string; textContent?: string } | null;
  getSelection?: () => Selection | null;
}

export function extractCourseId(text: string): string | null {
  const match = COURSE_ID_PATTERN.exec(text);
  return match ? match[0].toUpperCase() : null;
}

export function isVideoPageUrl(url: string): boolean {
  return VIDEO_HOST_PATTERN.test(url);
}

export function collectPageContext(doc: ReadableDocument = document): PageContext {
  const visibleText = (doc.body?.innerText || doc.body?.textContent || "").trim().slice(0, 5000);
  const selectedText = (doc.getSelection?.()?.toString() || "").trim().slice(0, 2000);
  const title = doc.title || "Untitled page";
  const url = doc.location.href;
  return {
    title,
    url,
    selectedText,
    visibleText,
    inferredCourseId: extractCourseId(`${title}\n${url}\n${visibleText}`),
    isVideoPage: isVideoPageUrl(url),
  };
}
