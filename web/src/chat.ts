import type { ChatMessage, Conversation } from "./types.js";

const STORAGE_PREFIX = "studylens.conv.";
const MAX_CONTEXT_TURNS = 3; // last 3 user/assistant pairs sent as context

function storageKey(courseId: string): string {
  return `${STORAGE_PREFIX}${courseId}`;
}

export function loadConversations(courseId: string): Conversation[] {
  try {
    const raw = localStorage.getItem(storageKey(courseId));
    if (!raw) return [];
    return JSON.parse(raw) as Conversation[];
  } catch {
    return [];
  }
}

export function saveConversations(courseId: string, convs: Conversation[]): void {
  try {
    localStorage.setItem(storageKey(courseId), JSON.stringify(convs));
  } catch {
    // Storage quota exceeded — silently ignore.
  }
}

export function createConversation(courseId: string): Conversation {
  return {
    id: crypto.randomUUID(),
    title: "New conversation",
    courseId,
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

export function addMessage(conv: Conversation, msg: Omit<ChatMessage, "id" | "timestamp">): ChatMessage {
  const full: ChatMessage = { ...msg, id: crypto.randomUUID(), timestamp: Date.now() };
  conv.messages.push(full);
  conv.updatedAt = Date.now();
  if (conv.messages.length === 1 && msg.role === "user") {
    conv.title = msg.content.slice(0, 48).trimEnd() + (msg.content.length > 48 ? "…" : "");
  }
  return full;
}

// Builds the question string sent to /ask, prefixing recent history as context.
export function buildQuestion(conv: Conversation, newQuestion: string): string {
  const history = conv.messages.slice(-(MAX_CONTEXT_TURNS * 2));
  if (history.length === 0) return newQuestion;

  const lines = history.map((m) =>
    m.role === "user" ? `Student: ${m.content}` : `Assistant: ${m.content}`
  );
  return `[Conversation context:\n${lines.join("\n")}\n]\n\nStudent question: ${newQuestion}`;
}
