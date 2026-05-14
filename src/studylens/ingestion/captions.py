from __future__ import annotations

import re
from dataclasses import dataclass

from studylens.domain import DocumentChunk, Resource

TIMECODE_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s+-->\s+"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class CaptionSegment:
    start_seconds: float
    end_seconds: float
    text: str


def parse_caption_segments(caption_text: str) -> list[CaptionSegment]:
    """Parse SRT or VTT captions into normalized text segments."""

    normalized = caption_text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", normalized)
    segments: list[CaptionSegment] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_index = next(
            (index for index, line in enumerate(lines) if TIMECODE_RE.search(line)),
            None,
        )
        if time_index is None:
            continue
        match = TIMECODE_RE.search(lines[time_index])
        if match is None:
            continue
        text = clean_caption_text(" ".join(lines[time_index + 1 :]))
        if not text:
            continue
        segments.append(
            CaptionSegment(
                start_seconds=parse_timecode(match.group("start")),
                end_seconds=parse_timecode(match.group("end")),
                text=text,
            )
        )
    return segments


def clean_caption_text(text: str) -> str:
    text = TAG_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_timecode(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds_part = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes:d}:{seconds_part:02d}"


def build_caption_chunks(
    resource: Resource,
    segments: list[CaptionSegment],
    *,
    max_chars: int = 1400,
) -> list[DocumentChunk]:
    if not segments:
        return []

    chunks: list[DocumentChunk] = []
    current_lines: list[str] = []
    current_start = segments[0].start_seconds
    current_end = segments[0].end_seconds

    def flush() -> None:
        nonlocal current_lines, current_start, current_end
        if not current_lines:
            return
        position = len(chunks)
        chunks.append(
            DocumentChunk(
                course_id=resource.course_id,
                resource_id=resource.id or "",
                kind="transcript",
                text="\n".join(current_lines),
                position=position,
                title=resource.title,
                source_url=resource.source_url,
                metadata={
                    **resource.metadata,
                    "start_seconds": current_start,
                    "end_seconds": current_end,
                },
            )
        )
        current_lines = []

    for segment in segments:
        line = (
            f"[{format_timestamp(segment.start_seconds)}-"
            f"{format_timestamp(segment.end_seconds)}] {segment.text}"
        )
        candidate_size = sum(len(existing) + 1 for existing in current_lines) + len(line)
        if current_lines and candidate_size > max_chars:
            flush()
            current_start = segment.start_seconds
        if not current_lines:
            current_start = segment.start_seconds
        current_end = segment.end_seconds
        current_lines.append(line)

    flush()
    return chunks

