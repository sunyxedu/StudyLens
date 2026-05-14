from __future__ import annotations

from dataclasses import dataclass

from studylens.generation.common import format_search_results, wrap_latex_document
from studylens.retrieval.qa import LLMClient, RAGService


@dataclass(slots=True)
class PredictedExamGenerator:
    rag: RAGService
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
        past_exam_results = self.rag.retrieve(
            "past exam paper questions marking style recurring topics likely assessment structure",
            course_id=course_id,
            kinds={"past_exam", "exercise", "tutorial", "material"},
            top_k=top_k,
        )
        scope_text = (
            "\n".join(f"- {note}" for note in scope_notes or [])
            or "- No scope notes supplied."
        )
        context = format_search_results(past_exam_results, max_chars=15000)
        prompt = f"""
Predict a plausible upcoming exam paper for {course_title} ({course_id}).

Rules:
- Produce {question_count} substantial questions with marks and subparts.
- Match style from past papers where evidence exists.
- Include a short rationale and a concise marking outline after each question.
- Do not claim certainty; label it as a prediction.
- Apply these scope notes:
{scope_text}

Past-paper and course context:
{context}
"""
        body = self.llm.complete(
            system="You generate careful, evidence-grounded predicted exam papers.",
            prompt=prompt.strip(),
        )
        if "\\documentclass" in body:
            return body
        return wrap_latex_document(f"{course_title} Predicted Paper", body)
