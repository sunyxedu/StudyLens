import type {
  Answer,
  AuthSession,
  AskRequest,
  AutoIndexCourseRequest,
  AutoIndexReport,
  BrowserStateStatus,
  DiscoverCoursesResponse,
  DiscoveryStatus,
  ForumBoard,
  ForumBoardCreateRequest,
  ForumBoardThreadsResponse,
  ForumCategory,
  ForumCategoryCreateRequest,
  ForumIndexResponse,
  ForumReplyCreateRequest,
  ForumThread,
  ForumThreadCreateRequest,
  GenerateRequest,
  IndexTextRequest,
  LoginRequest,
  PredictedExamRequest,
  RegisterRequest,
  RetrieveRequest,
  SearchResult,
} from "./types.js";

type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export class StudyLensApiError extends Error {
  readonly status: number;
  readonly body: string;
  readonly detail: string | null;

  constructor(status: number, body: string) {
    const detail = parseErrorDetail(body);
    super(`StudyLens API ${status}: ${detail ?? body}`);
    this.name = "StudyLensApiError";
    this.status = status;
    this.body = body;
    this.detail = detail;
  }
}

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

  async register(payload: RegisterRequest): Promise<AuthSession> {
    return this.request("/auth/register", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async login(payload: LoginRequest): Promise<AuthSession> {
    return this.request("/auth/login", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async session(): Promise<AuthSession> {
    return this.request("/auth/session");
  }

  async logout(): Promise<{ status: string }> {
    return this.request("/auth/logout", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async startBrowserState(): Promise<BrowserStateStatus> {
    return this.request("/browser-state/start", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async advanceBrowserState(): Promise<BrowserStateStatus> {
    return this.request("/browser-state/advance", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async browserStateStatus(): Promise<BrowserStateStatus> {
    return this.request("/browser-state/status");
  }

  async cancelBrowserState(): Promise<BrowserStateStatus> {
    return this.request("/browser-state/cancel", {
      method: "POST",
      headers: jsonHeaders(),
    });
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

  async listCourses(): Promise<{ courses: DiscoverCoursesResponse["courses"] }> {
    return this.request("/courses");
  }

  async discoverCourses(): Promise<DiscoverCoursesResponse> {
    return this.request("/courses/discover", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async startDiscovery(): Promise<DiscoveryStatus> {
    return this.request("/courses/discover/start", {
      method: "POST",
      headers: jsonHeaders(),
    });
  }

  async discoveryStatus(): Promise<DiscoveryStatus> {
    return this.request("/courses/discover/status");
  }

  async forumIndex(): Promise<ForumIndexResponse> {
    return this.request("/forum");
  }

  async createForumCategory(payload: ForumCategoryCreateRequest): Promise<ForumCategory> {
    return this.request("/forum/categories", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async createForumBoard(payload: ForumBoardCreateRequest): Promise<ForumBoard> {
    return this.request("/forum/boards", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async forumBoard(boardId: number): Promise<ForumBoardThreadsResponse> {
    return this.request(`/forum/boards/${boardId}`);
  }

  async createForumThread(payload: ForumThreadCreateRequest): Promise<ForumThread> {
    return this.request("/forum/threads", {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
    });
  }

  async forumThread(threadId: number): Promise<ForumThread> {
    return this.request(`/forum/threads/${threadId}`);
  }

  async createForumReply(
    threadId: number,
    payload: ForumReplyCreateRequest
  ): Promise<ForumThread> {
    return this.request(`/forum/threads/${threadId}/replies`, {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify(payload),
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
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        credentials: init?.credentials ?? "include",
      });
    } catch (error) {
      if (error instanceof TypeError) {
        throw new Error(`Cannot reach StudyLens API at ${this.baseUrl}`);
      }
      throw error;
    }
    if (!response.ok) {
      throw new StudyLensApiError(response.status, await safeText(response));
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

function parseErrorDetail(body: string): string | null {
  try {
    const data = JSON.parse(body) as { detail?: unknown };
    return typeof data.detail === "string" ? data.detail : null;
  } catch {
    return null;
  }
}
