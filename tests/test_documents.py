from pathlib import Path

import pytest

from studylens.domain import Resource
from studylens.errors import UnsupportedDocumentError
from studylens.ingestion.documents import build_chunks, chunk_text, extract_text


def test_extract_text_from_html_removes_noise(tmp_path: Path) -> None:
    path = tmp_path / "course.html"
    path.write_text(
        """
        <html><head><style>.x{}</style><script>ignored()</script></head>
        <body><nav>menu</nav><h1>Graph Search</h1><p>BFS and DFS.</p></body></html>
        """,
        encoding="utf-8",
    )

    text = extract_text(path)

    assert "Graph Search" in text
    assert "BFS and DFS" in text
    assert "ignored" not in text
    assert "menu" not in text


def test_chunk_text_keeps_overlap_and_validates_arguments() -> None:
    text = "A" * 80 + "\n\n" + "B" * 80 + "\n\n" + "C" * 80

    chunks = chunk_text(text, max_chars=170, overlap=10)

    assert len(chunks) >= 2
    assert chunks[0].endswith("B" * 80)
    assert chunks[1].startswith("B" * 10)
    with pytest.raises(ValueError):
        chunk_text("x", max_chars=10, overlap=10)


def test_build_chunks_uses_resource_metadata() -> None:
    resource = Resource(
        course_id="COMP70001",
        title="Tutorial 1",
        kind="tutorial",
        source_url="https://example.test/tutorial",
        metadata={"week": "1"},
    )

    chunks = build_chunks(
        resource,
        "First paragraph.\n\nSecond paragraph.",
        max_chars=20,
        overlap=5,
    )

    assert chunks
    assert chunks[0].course_id == "COMP70001"
    assert chunks[0].kind == "tutorial"
    assert chunks[0].metadata == {"week": "1"}


def test_extract_text_rejects_unknown_file_type(tmp_path: Path) -> None:
    path = tmp_path / "slides.bin"
    path.write_bytes(b"\x00\x01")

    with pytest.raises(UnsupportedDocumentError):
        extract_text(path)
