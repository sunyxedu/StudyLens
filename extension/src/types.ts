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

export interface AskRequest {
  question: string;
  course_id?: string | null;
  top_k?: number;
  include_exercises?: boolean;
}

export interface PageContext {
  title: string;
  url: string;
  selectedText: string;
  visibleText: string;
  inferredCourseId?: string | null;
}

export interface StudyLensSettings {
  backendUrl: string;
}

