export type ResourceKind =
  | "material"
  | "exercise"
  | "tutorial"
  | "video"
  | "transcript"
  | "edstem_note"
  | "past_exam"
  | "generated";

export interface Citation {
  course_id: string;
  resource_id: string;
  title?: string | null;
  source_url?: string | null;
  position?: number | null;
  quote?: string | null;
  page?: number | null;
  start_seconds?: number | null;
}

export interface Answer {
  question: string;
  answer: string;
  citations: Citation[];
  follow_up?: string | null;
}

export interface SearchResult {
  chunk: {
    id?: string | null;
    course_id: string;
    resource_id: string;
    kind: ResourceKind;
    text: string;
    position: number;
    title?: string | null;
    source_url?: string | null;
    metadata: Record<string, unknown>;
  };
  score: number;
}

export interface IndexTextRequest {
  course_id: string;
  title: string;
  text: string;
  kind: ResourceKind;
}

export interface RegisterRequest {
  username: string;
  grade: string;
  course: string;
  password: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface AuthUser {
  id: number;
  username: string;
  grade: string;
  course: string;
  is_admin?: boolean;
}

export interface AuthSession {
  user: AuthUser;
  created: boolean;
  browser_state_ready: boolean;
  needs_browser_state: boolean;
}

export interface BrowserStateStep {
  key: string;
  title: string;
  url: string;
  instruction: string;
}

export interface BrowserStateStatus {
  running: boolean;
  completed: boolean;
  ready: boolean;
  total_steps: number;
  step_index?: number | null;
  step?: BrowserStateStep | null;
  error?: string | null;
}

export interface AutoIndexCourseRequest {
  course_id: string;
  course_title: string;
}

export interface AutoIndexItem {
  title: string;
  kind: ResourceKind;
  status: "indexed" | "skipped" | "failed" | string;
  stage?: "scientia" | "panopto" | "exams" | "edstem" | string;
  source_url?: string | null;
  local_path?: string | null;
  chunks: number;
  error?: string | null;
}

export interface AutoIndexReport {
  course_id: string;
  course_title: string;
  source_url?: string | null;
  discovered_resources: number;
  indexed_resources: number;
  indexed_chunks: number;
  items: AutoIndexItem[];
}

export interface AskRequest {
  question: string;
  course_id?: string | null;
  top_k: number;
  include_exercises: boolean;
}

export interface RetrieveRequest {
  query: string;
  course_id?: string | null;
  kinds?: ResourceKind[];
  top_k: number;
}

export interface GenerateRequest {
  course_id: string;
  course_title: string;
  scope_notes: string[];
  top_k: number;
}

export interface PredictedExamRequest extends GenerateRequest {
  question_count: number;
}

export interface DiscoveredCourse {
  code: string;
  title: string;
  edstem_url?: string | null;
  updated_at?: string | null;
  indexed_at?: string | null;
}

export interface DiscoverCoursesResponse {
  courses: DiscoveredCourse[];
  dropped_titles: string[];
  num_turns: number;
  total_cost_usd: number;
  error?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  timestamp: number;
}

export interface Conversation {
  id: string;
  title: string;
  courseId: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}

export interface ForumCategory {
  id: number;
  name: string;
  slug: string;
  description: string;
  color: string;
  created_by_username?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ForumBoard {
  id: number;
  category_id: number;
  category_name: string;
  name: string;
  slug: string;
  description: string;
  created_by_username?: string | null;
  thread_count: number;
  reply_count: number;
  latest_activity_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ForumCategoryWithBoards extends ForumCategory {
  boards: ForumBoard[];
}

export interface ForumThreadSummary {
  id: number;
  board_id: number;
  board_name: string;
  category_id: number;
  category_name: string;
  title: string;
  body_preview: string;
  course_id?: string | null;
  author_username: string;
  author_role: "student" | "admin" | "bot" | string;
  is_anonymous: boolean;
  is_read: boolean;
  reply_count: number;
  dylen_replied: boolean;
  created_at: string;
  updated_at: string;
  latest_activity_at: string;
}

export interface ForumReply {
  id: number;
  thread_id: number;
  author_username: string;
  author_role: "student" | "admin" | "bot" | string;
  is_anonymous: boolean;
  body: string;
  citations: Citation[];
  created_at: string;
}

export interface ForumThread extends ForumThreadSummary {
  body: string;
  replies: ForumReply[];
}

export interface ForumIndexResponse {
  categories: ForumCategoryWithBoards[];
  can_create_categories: boolean;
}

export interface ForumBoardThreadsResponse {
  board: ForumBoard;
  threads: ForumThreadSummary[];
}

export interface ForumCategoryCreateRequest {
  name: string;
  description: string;
  color?: string | null;
}

export interface ForumBoardCreateRequest {
  category_id: number;
  name: string;
  description: string;
}

export interface ForumThreadCreateRequest {
  board_id: number;
  title: string;
  body: string;
  course_id?: string | null;
  anonymous?: boolean;
}

export interface ForumReplyCreateRequest {
  body: string;
  anonymous?: boolean;
}
