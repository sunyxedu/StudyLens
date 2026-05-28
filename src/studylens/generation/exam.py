from __future__ import annotations

from dataclasses import dataclass

from studylens.generation.common import (
    CourseContextProvider,
    auto_scope_notes,
    format_scope_notes,
    wrap_latex_document,
)
from studylens.retrieval.qa import LLMClient


@dataclass(slots=True)
class PredictedExamGenerator:
    context_provider: CourseContextProvider
    llm: LLMClient

    def generate(
        self,
        *,
        course_id: str,
        course_title: str,
        scope_notes: list[str] | None = None,
        question_count: int = 4,
        top_k: int = 50,
    ) -> str:
        del top_k
        notes = (
            scope_notes
            if scope_notes
            else auto_scope_notes(self.context_provider, course_id=course_id)
        )
        context = self.context_provider.format_course_context(
            course_id=course_id,
            kinds=None,
            max_chars=180_000,
        )
        prompt = f"""
Predict a plausible upcoming exam paper for {course_title} ({course_id}).

Rules:
- Produce {question_count} substantial questions with marks and subparts.
- Match style from past papers where evidence exists.
- Include a short rationale and a concise marking outline after each question.
- Do not claim certainty; label it as a prediction.
- Apply these scope notes:
{format_scope_notes(notes)}

Full local course-file context:
{context}
"""
        body = self.llm.complete(
            system="You generate careful, evidence-grounded predicted exam papers.",
            prompt=prompt.strip(),
        )
        if "\\documentclass" in body:
            return body
        return wrap_latex_document(f"{course_title} Predicted Paper", body)
