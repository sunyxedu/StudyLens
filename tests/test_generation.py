from pathlib import Path

from studylens.generation import CheatsheetGenerator, PredictedExamGenerator
from studylens.generation.common import ManifestCourseContextProvider
from studylens.ingestion.manifest import CourseManifest, ManifestItem, write_manifest


class RecordingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, *, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        return (
            "\\subsection*{Core}\\begin{itemize}"
            "\\item Memoization avoids repeated work.\\end{itemize}"
        )


class RecordingContextProvider:
    def __init__(self, *, scope_notes: list[str] | None = None) -> None:
        self.scope_note_values = scope_notes or []
        self.requests: list[tuple[str, set[str] | None, int]] = []

    def format_course_context(
        self,
        *,
        course_id: str,
        kinds: set[str] | None = None,
        max_chars: int = 180_000,
    ) -> str:
        self.requests.append((course_id, kinds, max_chars))
        return (
            "[1] Lecture notes (material)\n"
            "Dynamic programming, recurrence relations, optimal substructure.\n\n"
            "[2] 2024 Paper (past_exam)\n"
            "Question 1 asks for a recurrence and complexity analysis."
        )

    def scope_notes(self, *, course_id: str, max_chars: int = 12_000) -> list[str]:
        return self.scope_note_values


def test_cheatsheet_generator_wraps_latex_and_includes_scope_notes() -> None:
    llm = RecordingLLM()
    provider = RecordingContextProvider()
    generator = CheatsheetGenerator(context_provider=provider, llm=llm)

    latex = generator.generate(
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        scope_notes=["Network flow is not assessed."],
    )

    assert latex.startswith("\\documentclass")
    assert "\\begin{multicols*}{2}" in latex
    assert "Advanced Algorithms Cheatsheet" in latex
    assert "Network flow is not assessed" in llm.prompts[0]
    assert provider.requests[0][1] is None


def test_predicted_exam_generator_infers_structure_from_papers_and_notes() -> None:
    llm = RecordingLLM()
    provider = RecordingContextProvider()
    generator = PredictedExamGenerator(context_provider=provider, llm=llm)

    latex = generator.generate(
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        scope_notes=["Only weeks 1-8 are examinable."],
    )

    prompt = llm.prompts[0]
    assert "Predicted Paper" in latex
    assert "Inferred past-paper format" in prompt
    assert "typical number of questions" in prompt
    assert "main question types" in prompt
    assert "Do not ask for, assume, or mention a user-specified question count" in prompt
    assert "Lecture-note context" in prompt
    assert "Only weeks 1-8" in prompt
    assert provider.requests == [
        ("COMP70001", {"past_exam"}, 90_000),
        ("COMP70001", {"material", "transcript"}, 70_000),
        ("COMP70001", {"exercise", "tutorial"}, 30_000),
    ]


def test_cheatsheet_generator_auto_pulls_local_edstem_scope_notes() -> None:
    llm = RecordingLLM()
    provider = RecordingContextProvider(
        scope_notes=["Network flow is not examinable this year."]
    )
    generator = CheatsheetGenerator(context_provider=provider, llm=llm)

    generator.generate(course_id="COMP70001", course_title="Advanced Algorithms")

    prompt = llm.prompts[-1]
    assert "Network flow is not examinable this year" in prompt


def test_explicit_scope_notes_override_indexed_edstem_notes() -> None:
    llm = RecordingLLM()
    provider = RecordingContextProvider(scope_notes=["Network flow is not examinable."])
    generator = CheatsheetGenerator(context_provider=provider, llm=llm)

    generator.generate(
        course_id="COMP70001",
        course_title="Advanced Algorithms",
        scope_notes=["Override: include greedy algorithms only."],
    )

    prompt = llm.prompts[-1]
    assert "Override: include greedy algorithms only" in prompt
    assert "Network flow is not examinable" not in prompt


def test_manifest_context_provider_reads_local_course_files(tmp_path: Path) -> None:
    course_dir = tmp_path / "COMP70001"
    (course_dir / "material").mkdir(parents=True)
    (course_dir / "edstem_note").mkdir()
    (course_dir / "material" / "notes.txt").write_text(
        "Dynamic programming and greedy algorithms.",
        encoding="utf-8",
    )
    (course_dir / "edstem_note" / "scope.txt").write_text(
        "Network flow is not examinable.",
        encoding="utf-8",
    )
    write_manifest(
        tmp_path,
        CourseManifest(
            course_id="COMP70001",
            course_title="Advanced Algorithms",
            course_url=None,
            crawled_at="2026-01-01T00:00:00+00:00",
            items=[
                ManifestItem(
                    source_url="https://example.com/notes",
                    local_path="material/notes.txt",
                    kind="material",
                    title="Lecture notes",
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    metadata={"stage": "scientia"},
                ),
                ManifestItem(
                    source_url="https://example.com/scope",
                    local_path="edstem_note/scope.txt",
                    kind="edstem_note",
                    title="Scope note",
                    downloaded_at="2026-01-01T00:00:00+00:00",
                    metadata={"stage": "edstem"},
                ),
            ],
        ),
    )
    provider = ManifestCourseContextProvider(tmp_path)

    context = provider.format_course_context(
        course_id="COMP70001",
        kinds={"material"},
    )

    assert "Lecture notes" in context
    assert "Dynamic programming and greedy algorithms" in context
    assert provider.scope_notes(course_id="COMP70001") == ["Network flow is not examinable."]
