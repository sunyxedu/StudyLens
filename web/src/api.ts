import type {
  Answer,
  AskRequest,
  AutoIndexCourseRequest,
  AutoIndexReport,
  DiscoverCoursesResponse,
  GenerateRequest,
  IndexTextRequest,
  PredictedExamRequest,
  RetrieveRequest,
  SearchResult,
} from "./types.js";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim();
  return (trimmed || "http://localhost:8000").replace(/\/+$/, "");
}

export class StudyLensApi {
  readonly baseUrl: string;
  private readonly fetchImpl: FetchLike;

  constructor(baseUrl: string, fetchImpl: FetchLike = (input, init) => fetch(input, init)) {
    this.baseUrl = normalizeBaseUrl(baseUrl);
    this.fetchImpl = fetchImpl;
  }

  async health(): Promise<{ status: string; vector_store: string }> {
    return this.request("/health");
  }

  async indexText(payload: IndexTextRequest): Promise<{ indexed_chunks: number }> {
    return this.request("/chunks", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async autoIndexCourse(payload: AutoIndexCourseRequest): Promise<AutoIndexReport> {
    return this.request("/index/course", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async discoverCourses(): Promise<DiscoverCoursesResponse> {
    return this.request("/courses/discover", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async ask(payload: AskRequest): Promise<Answer> {
    return this.request("/ask", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async retrieve(payload: RetrieveRequest): Promise<{ results: SearchResult[] }> {
    return this.request("/retrieve", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async generateCheatsheet(payload: GenerateRequest): Promise<{ latex: string }> {
    return this.request("/generate/cheatsheet", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async generatePredictedExam(payload: PredictedExamRequest): Promise<{ latex: string }> {
    return this.request("/generate/predicted-exam", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, init);
    if (!response.ok) {
      throw new Error(`StudyLens API ${response.status}: ${await safeText(response)}`);
    }
    return (await response.json()) as T;
  }
}

function jsonHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return response.statusText;
  }
}
