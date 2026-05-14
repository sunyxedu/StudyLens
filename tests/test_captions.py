from studylens.domain import Resource
from studylens.ingestion.captions import (
    build_caption_chunks,
    format_timestamp,
    parse_caption_segments,
)


def test_parse_srt_and_vtt_caption_segments() -> None:
    captions = """WEBVTT

1
00:00:01.000 --> 00:00:03.500
<v Speaker>Dynamic programming stores subproblems.

2
00:00:04,000 --> 00:00:06,000
Memoization avoids repeated work.
"""

    segments = parse_caption_segments(captions)

    assert len(segments) == 2
    assert segments[0].start_seconds == 1.0
    assert segments[0].end_seconds == 3.5
    assert segments[0].text == "Dynamic programming stores subproblems."
    assert segments[1].text == "Memoization avoids repeated work."


def test_build_caption_chunks_include_timestamps_and_video_metadata() -> None:
    resource = Resource(
        course_id="COMP70001",
        title="Lecture 1 captions",
        kind="transcript",
        source_url="https://panopto.test/viewer?id=session",
        metadata={"session_id": "session", "video_url": "https://panopto.test/viewer"},
    )
    segments = parse_caption_segments(
        """1
00:00:01,000 --> 00:00:03,000
First line.

2
00:00:04,000 --> 00:00:05,000
Second line.
"""
    )

    chunks = build_caption_chunks(resource, segments, max_chars=80)

    assert chunks
    assert chunks[0].kind == "transcript"
    assert "[0:01-0:03] First line." in chunks[0].text
    assert chunks[0].metadata["session_id"] == "session"
    assert chunks[0].metadata["start_seconds"] == 1.0
    assert chunks[-1].metadata["end_seconds"] == 5.0
    assert format_timestamp(3661) == "1:01:01"

