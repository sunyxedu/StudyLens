"""SQLite-backed cache of discovered courses.

Discovery is expensive (an agent + a browser session, ~$0.10 a run) and the
result barely changes between sessions, so we keep the last list on disk
and only re-run when the user explicitly clicks Discover. The DB lives at
the `database_url` configured in settings.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CourseRecord:
    code: str
    title: str
    edstem_url: str | None
    updated_at: str  # ISO-8601 UTC
    indexed_at: str | None = None  # ISO-8601 UTC, set after a successful auto-index


def _sqlite_path(database_url: str) -> Path:
    """Resolve the SQLite database path from a SQLAlchemy-style URL.

    Accepts `sqlite:///relative/path.db`, `sqlite:////absolute/path.db`, or
    a bare filesystem path. Anything else raises.
    """
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    if database_url.startswith("sqlite://"):
        return Path(database_url.removeprefix("sqlite://"))
    if "://" not in database_url:
        return Path(database_url)
    raise ValueError(f"Unsupported database_url: {database_url!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class CourseStore:
    """Thin sqlite3 wrapper for the courses cache."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_database_url(cls, database_url: str) -> CourseStore:
        return cls(_sqlite_path(database_url))

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS courses (
                    code TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    edstem_url TEXT,
                    updated_at TEXT NOT NULL,
                    indexed_at TEXT
                )
                """
            )
            with contextlib.suppress(sqlite3.OperationalError):
                connection.execute("ALTER TABLE courses ADD COLUMN indexed_at TEXT")

    def list_all(self) -> list[CourseRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT code, title, edstem_url, updated_at, indexed_at FROM courses ORDER BY code"
            ).fetchall()
        return [
            CourseRecord(
                code=row["code"],
                title=row["title"],
                edstem_url=row["edstem_url"],
                updated_at=row["updated_at"],
                indexed_at=row["indexed_at"],
            )
            for row in rows
        ]

    def replace_all(self, courses: Iterable[tuple[str, str, str | None]]) -> list[CourseRecord]:
        """Wipe and repopulate the table.

        EdStem dashboard is the source of truth — courses the student is no
        longer enrolled in should disappear. The fresh updated_at timestamp
        also doubles as "this is when we last verified the enrolment".
        indexed_at is preserved for courses that survive the replace.
        """
        timestamp = _now()
        course_list = list(courses)
        with self._connect() as connection, connection:
            existing_indexed = {
                row["code"]: row["indexed_at"]
                for row in connection.execute(
                    "SELECT code, indexed_at FROM courses"
                ).fetchall()
            }
            connection.execute("DELETE FROM courses")
            if course_list:
                connection.executemany(
                    "INSERT INTO courses (code, title, edstem_url, updated_at, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [
                        (code, title, url, timestamp, existing_indexed.get(code))
                        for code, title, url in course_list
                    ],
                )
        return [
            CourseRecord(
                code=code,
                title=title,
                edstem_url=url,
                updated_at=timestamp,
                indexed_at=existing_indexed.get(code),
            )
            for code, title, url in course_list
        ]

    def mark_indexed(self, code: str) -> None:
        """Record the current UTC time as the last indexed timestamp for a course."""
        with self._connect() as connection, connection:
            connection.execute(
                "UPDATE courses SET indexed_at = ? WHERE code = ?",
                (_now(), code),
            )
