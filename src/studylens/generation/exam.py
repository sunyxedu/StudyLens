from __future__ import annotations

from dataclasses import dataclass

from studylens.generation.common import (
    CourseContextProvider,
    auto_scope_notes,
    format_scope_notes,
    wrap_latex_document,
)
from studylens.retrieval.qa import LLMClient

LECTURE_NOTE_KINDS = {"material", "transcript"}
PAST_PAPER_KINDS = {"past_exam"}
SUPPORTING_KINDS = {"exercise", "tutorial"}


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
        top_k: int = 50,
    ) -> str:
        del top_k
        notes = (
            scope_notes
            if scope_notes
            else auto_scope_notes(self.context_provider, course_id=course_id)
        )
        past_paper_context = self.context_provider.format_course_context(
            course_id=course_id,
            kinds=PAST_PAPER_KINDS,
            max_chars=90_000,
        )
        lecture_context = self.context_provider.format_course_context(
            course_id=course_id,
            kinds=LECTURE_NOTE_KINDS,
            max_chars=70_000,
        )
        supporting_context = self.context_provider.format_course_context(
            course_id=course_id,
            kinds=SUPPORTING_KINDS,
            max_chars=30_000,
        )
        prompt = f"""
Predict a plausible upcoming exam paper for {course_title} ({course_id}).

Rules:
- First infer the historical paper structure from the past-paper context.
- Include an "Inferred past-paper format" summary covering:
  1. The typical number of questions or question-count range.
  2. The main question types and subpart style.
  3. The visible mark-allocation pattern, if present.
- Use that inferred historical structure to decide how many questions to generate.
- Do not ask for, assume, or mention a user-specified question count.
- Extract examinable knowledge points from the lecture-note context before writing
  questions, and prefer topics that are strongly represented there.
- Match style from past papers where evidence exists.
- Include a short rationale and concise marking outline after each question.
- Do not claim certainty; label it as a prediction.
- Apply these scope notes:
{format_scope_notes(notes)}

Past-paper context:
{past_paper_context}

Lecture-note context:
{lecture_context}

Supporting exercise/tutorial context:
{supporting_context}
"""
        body = self.llm.complete(
            system=(
                "You infer exam-paper structure from past papers and generate "
                "careful, evidence-grounded predicted exam papers."
            ),
            prompt=prompt.strip(),
        )
        if "\\documentclass" in body:
            return body
        return wrap_latex_document(f"{course_title} Predicted Paper", body)
