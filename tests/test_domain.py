from studylens.domain import DocumentChunk, Resource


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

