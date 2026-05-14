import type { Answer, AskRequest } from "./types.js";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export function normalizeBaseUrl(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) {
    return "http://localhost:8000";
  }
  return trimmed.replace(/\/+$/, "");
}

export class StudyLensClient {
  readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;

  constructor(baseUrl: string, fetchImpl: FetchLike = fetch) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.fetchImpl = fetchImpl;
  }

  async health(): Promise<boolean> {
    const response = await this.fetchImpl(`${this.baseUrl}/health`);
    return response.ok;
  }

  async ask(request: AskRequest): Promise<Answer> {
    const response = await this.fetchImpl(`${this.baseUrl}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        top_k: 5,
        include_exercises: false,
        ...request,
      }),
    });
    if (!response.ok) {
      const message = await safeErrorText(response);
      throw new Error(`StudyLens API error ${response.status}: ${message}`);
    }
    return (await response.json()) as Answer;
  }
}

async function safeErrorText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return response.statusText;
  }
}
