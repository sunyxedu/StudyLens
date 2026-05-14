from __future__ import annotations

from dataclasses import dataclass

from studylens.generation.common import format_search_results, wrap_latex_document
from studylens.retrieval.qa import LLMClient, RAGService


@dataclass(slots=True)
class CheatsheetGenerator:
    rag: RAGService
    llm: LLMClient

    def generate(
        self,
        *,
        course_id: str,
        course_title: str,
        scope_notes: list[str] | None = None,
        top_k: int = 40,
    ) -> str:
        results = self.rag.retrieve(
            "all examinable definitions theorems algorithms methods pitfalls formulas examples",
            course_id=course_id,
            top_k=top_k,
        )
        scope_text = "\n".join(f"- {note}" for note in scope_notes or []) or "- No scope notes supplied."
        context = format_search_results(results, max_chars=14000)
        prompt = f"""
Create a dense two-page A4 LaTeX cheatsheet for {course_title} ({course_id}).

Rules:
- Output only LaTeX body content that can go inside a two-column article document.
- Be comprehensive and compact.
- Include definitions, formulas, algorithm steps, assumptions, complexity, common traps, and miniature examples.
- Respect these EdStem/exam-scope notes:
{scope_text}

Course context:
{context}
"""
        body = self.llm.complete(
            system="You create precise compact Imperial Computing revision cheatsheets.",
            prompt=prompt.strip(),
        )
        if "\\documentclass" in body:
            return body
        return wrap_latex_document(f"{course_title} Cheatsheet", body)

