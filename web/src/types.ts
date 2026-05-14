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

export interface AutoIndexCourseRequest {
  course_id: string;
  course_title?: string | null;
  course_url?: string | null;
}

export interface AutoIndexItem {
  title: string;
  kind: ResourceKind;
  status: "indexed" | "skipped" | "failed" | string;
  stage?: "scientia" | "panopto" | string;
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
