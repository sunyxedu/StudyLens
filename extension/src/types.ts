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

export type ResourceKind =
  | "material"
  | "exercise"
  | "tutorial"
  | "video"
  | "transcript"
  | "edstem_note"
  | "past_exam"
  | "generated";

export interface AskRequest {
  question: string;
  course_id?: string | null;
  kinds?: ResourceKind[];
  top_k?: number;
  include_exercises?: boolean;
}

export interface PageContext {
  title: string;
  url: string;
  selectedText: string;
  visibleText: string;
  inferredCourseId?: string | null;
  isVideoPage?: boolean;
}

export interface StudyLensSettings {
  backendUrl: string;
}

