from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from studylens.bootstrap import build_rag_service
from studylens.config import get_settings
from studylens.domain import CourseSummary, Resource
from studylens.generation import CheatsheetGenerator, PredictedExamGenerator
from studylens.ingestion.auto_index import build_auto_indexer
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.documents import build_chunks, extract_text
from studylens.ingestion.scientia import parse_course_page, parse_timeline

app = typer.Typer(help="StudyLens course ingestion and retrieval CLI.")


@app.command()
def inspect_scientia(
    html_file: Path,
    base_url: str = "https://scientia.doc.ic.ac.uk/2526/timeline",
) -> None:
    """Parse a saved Scientia timeline HTML file."""

    courses = parse_timeline(html_file.read_text(encoding="utf-8"), base_url)
    typer.echo(json.dumps([course.model_dump() for course in courses], indent=2))


@app.command()
def inspect_course(
    html_file: Path,
    course_id: str,
    title: str,
    base_url: str,
) -> None:
    """Parse a saved Scientia course page."""

    summary = CourseSummary(id=course_id, title=title, url=base_url)
    course = parse_course_page(html_file.read_text(encoding="utf-8"), summary, base_url)
    typer.echo(course.model_dump_json(indent=2))


@app.command()
def index_text(
    course_id: str,
    path: Path,
    title: str | None = None,
    kind: str = "material",
) -> None:
    """Extract, chunk, embed, and index a local text-like document."""

    settings = get_settings()
    service = build_rag_service(settings)
    text = extract_text(path)
    resource = Resource(
        course_id=course_id,
        title=title or path.stem,
        kind=kind,
        local_path=path,
    )
    chunks = build_chunks(resource, text)
    indexed = service.index_chunks(chunks)
    typer.echo(f"Indexed {indexed} chunks for {course_id}.")


@app.command("auto-index")
def auto_index(
    course_id: str,
    course_title: str | None = None,
    course_url: str | None = None,
) -> None:
    """Discover a Scientia course page, download resources, and index what can be parsed."""

    settings = get_settings()
    service = build_rag_service(settings)

    async def _run() -> str:
        async with BrowserSession.from_settings(settings) as session:
            indexer = build_auto_indexer(settings, service, session)
            report = await indexer.index_course(
                course_id=course_id,
                course_title=course_title,
                course_url=course_url,
            )
            return report.model_dump_json(indent=2)

    typer.echo(asyncio.run(_run()))


@app.command()
def ask(
    question: str,
    course_id: str | None = None,
    top_k: int = 5,
) -> None:
    """Ask StudyLens using the indexed course context."""

    service = build_rag_service(get_settings())
    answer = service.answer(question, course_id=course_id, top_k=top_k)
    typer.echo(answer.answer)
    if answer.citations:
        typer.echo("\nCitations:")
        for citation in answer.citations:
            typer.echo(f"- {citation.title or citation.resource_id} chunk {citation.position}")


@app.command("generate-cheatsheet")
def generate_cheatsheet(course_id: str, course_title: str, output: Path) -> None:
    """Generate a compact LaTeX cheatsheet from indexed context."""

    service = build_rag_service(get_settings())
    latex = CheatsheetGenerator(rag=service, llm=service.llm).generate(
        course_id=course_id,
        course_title=course_title,
    )
    output.write_text(latex, encoding="utf-8")
    typer.echo(f"Wrote {output}.")


@app.command("generate-predicted-exam")
def generate_predicted_exam(course_id: str, course_title: str, output: Path) -> None:
    """Generate a predicted exam paper from indexed context."""

    service = build_rag_service(get_settings())
    latex = PredictedExamGenerator(rag=service, llm=service.llm).generate(
        course_id=course_id,
        course_title=course_title,
    )
    output.write_text(latex, encoding="utf-8")
    typer.echo(f"Wrote {output}.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the StudyLens API."""

    import uvicorn

    uvicorn.run("studylens.api.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    app()
