from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from studylens.domain import Resource
from studylens.errors import ConfigurationError, UnsupportedDocumentError


@dataclass(slots=True)
class TranscriptExtractor:
    """Create transcript resources from local media files or existing transcript files."""

    transcript_dir: Path = Path("data/processed/transcripts")

    def from_text_file(self, course_id: str, path: Path, title: str | None = None) -> Resource:
        if path.suffix.lower() not in {".txt", ".md", ".vtt", ".srt"}:
            raise UnsupportedDocumentError(f"Not a transcript-like file: {path}")
        return Resource(
            course_id=course_id,
            title=title or path.stem,
            kind="transcript",
            local_path=path,
            metadata={"source": "transcript_file"},
        )

    def transcribe_with_openai(
        self,
        course_id: str,
        media_path: Path,
        api_key: str | None,
    ) -> Resource:
        if not api_key:
            raise ConfigurationError("OpenAI API key is required for transcription")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigurationError("Install openai to transcribe media") from exc

        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        client = OpenAI(api_key=api_key)
        with media_path.open("rb") as media:
            transcript = client.audio.transcriptions.create(model="whisper-1", file=media)
        text = getattr(transcript, "text", str(transcript))
        output = self.transcript_dir / f"{course_id}-{media_path.stem}.txt"
        output.write_text(text, encoding="utf-8")
        return Resource(
            course_id=course_id,
            title=media_path.stem,
            kind="transcript",
            local_path=output,
            metadata={"source": "openai_transcription", "media_path": str(media_path)},
        )
