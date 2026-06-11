from pathlib import Path

from studylens.domain import DocumentChunk, Resource


def test_resource_id_survives_path_and_title_changes() -> None:
    first = Resource(
        course_id="COMP50001",
        title="Notes",
        kind="material",
        source_url="https://scientia.doc.ic.ac.uk/notes.pdf",
        local_path=Path("/run1/notes.pdf"),
    )
    second = Resource(
        course_id="COMP50001",
        title="Notes.pdf",
        kind="material",
        source_url="https://scientia.doc.ic.ac.uk/notes.pdf",
        local_path=Path("/run2/notes (1).pdf"),
    )

    assert first.id == second.id


def test_resource_id_falls_back_to_basename_then_title() -> None:
    by_path_a = Resource(
        course_id="C", title="Notes", kind="material", local_path=Path("/a/notes.pdf")
    )
    by_path_b = Resource(
        course_id="C", title="Other", kind="material", local_path=Path("/b/notes.pdf")
    )
    titled_only = Resource(course_id="C", title="Untracked", kind="material")

    assert by_path_a.id == by_path_b.id
    assert titled_only.id
    assert titled_only.id != by_path_a.id


def test_resource_and_chunk_ids_are_stable() -> None:
    resource = Resource(course_id="COMP70001", title="Lecture 1", kind="material")
    same_resource = Resource(course_id="COMP70001", title="Lecture 1", kind="material")

    assert resource.id == same_resource.id

    chunk = DocumentChunk(
        course_id="COMP70001",
        resource_id=resource.id or "",
        kind="material",
        text="Dynamic programming stores overlapping subproblems.",
        position=0,
    )
    same_chunk = DocumentChunk(
        course_id="COMP70001",
        resource_id=resource.id or "",
        kind="material",
        text="Dynamic programming stores overlapping subproblems.",
        position=0,
    )

    assert chunk.id == same_chunk.id

