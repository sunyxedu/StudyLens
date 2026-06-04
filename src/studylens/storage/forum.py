"""SQLite-backed StudyLens forum storage."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from studylens.domain import Citation
from studylens.storage.courses import _sqlite_path

ForumRole = Literal["student", "admin", "bot"]

DEFAULT_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    (
        "Mathematics",
        "Problem solving, proofs, statistics, algebra, analysis, and exam technique.",
        "#2e5d4d",
    ),
    (
        "Computer Science",
        "Algorithms, systems, AI, software engineering, theory, and project help.",
        "#566884",
    ),
    (
        "Physics",
        "Mechanics, electromagnetism, quantum topics, labs, and problem sheets.",
        "#8a5560",
    ),
    (
        "Study Skills",
        "Revision planning, note-taking, exam routines, and student-to-student advice.",
        "#b07d35",
    ),
)


class ForumStoreError(ValueError):
    """Raised when forum inputs are invalid."""


@dataclass(frozen=True, slots=True)
class ForumCategoryRecord:
    id: int
    name: str
    slug: str
    description: str
    color: str
    created_by: int | None
    created_by_username: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ForumBoardRecord:
    id: int
    category_id: int
    category_name: str
    name: str
    slug: str
    description: str
    created_by: int | None
    created_by_username: str | None
    thread_count: int
    reply_count: int
    latest_activity_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ForumThreadSummaryRecord:
    id: int
    board_id: int
    board_name: str
    category_id: int
    category_name: str
    title: str
    body_preview: str
    course_id: str | None
    author_id: int
    author_username: str
    author_role: ForumRole
    is_anonymous: bool
    reply_count: int
    dylen_replied: bool
    created_at: str
    updated_at: str
    latest_activity_at: str


@dataclass(frozen=True, slots=True)
class ForumReplyRecord:
    id: int
    thread_id: int
    author_id: int | None
    author_username: str
    author_role: ForumRole
    is_anonymous: bool
    body: str
    citations: list[Citation]
    created_at: str


@dataclass(frozen=True, slots=True)
class ForumThreadRecord:
    id: int
    board_id: int
    board_name: str
    category_id: int
    category_name: str
    title: str
    body: str
    course_id: str | None
    author_id: int
    author_username: str
    author_role: ForumRole
    is_anonymous: bool
    reply_count: int
    dylen_replied: bool
    created_at: str
    updated_at: str
    latest_activity_at: str
    replies: list[ForumReplyRecord]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return slug or "board"


def _clean_text(
    value: str,
    name: str,
    *,
    max_length: int,
    min_length: int = 1,
) -> str:
    cleaned = re.sub(r"\r\n?", "\n", value).strip()
    if len(cleaned) < min_length:
        raise ForumStoreError(f"{name} is required")
    if len(cleaned) > max_length:
        raise ForumStoreError(f"{name} is too long")
    return cleaned


def _clean_color(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return "#566884"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", cleaned):
        raise ForumStoreError("color must be a hex color like #566884")
    return cleaned.lower()


def _clean_course_id(value: str | None) -> str | None:
    cleaned = (value or "").strip().upper()
    if not cleaned:
        return None
    if len(cleaned) > 32:
        raise ForumStoreError("course id is too long")
    return cleaned


def _body_preview(body: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", body).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"


def _citations_from_json(value: str | None) -> list[Citation]:
    if not value:
        return []
    try:
        raw = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    citations: list[Citation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            citations.append(Citation.model_validate(item))
        except ValueError:
            continue
    return citations


def _citations_to_json(citations: list[Citation] | None) -> str:
    return json.dumps(
        [citation.model_dump() for citation in citations or []],
        separators=(",", ":"),
        sort_keys=True,
    )


def _category_from_row(row: sqlite3.Row) -> ForumCategoryRecord:
    return ForumCategoryRecord(
        id=int(row["id"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        description=str(row["description"]),
        color=str(row["color"]),
        created_by=row["created_by"],
        created_by_username=row["created_by_username"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _board_from_row(row: sqlite3.Row) -> ForumBoardRecord:
    return ForumBoardRecord(
        id=int(row["id"]),
        category_id=int(row["category_id"]),
        category_name=str(row["category_name"]),
        name=str(row["name"]),
        slug=str(row["slug"]),
        description=str(row["description"]),
        created_by=row["created_by"],
        created_by_username=row["created_by_username"],
        thread_count=int(row["thread_count"]),
        reply_count=int(row["reply_count"]),
        latest_activity_at=row["latest_activity_at"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _thread_summary_from_row(row: sqlite3.Row) -> ForumThreadSummaryRecord:
    is_anon = bool(row["is_anonymous"])
    return ForumThreadSummaryRecord(
        id=int(row["id"]),
        board_id=int(row["board_id"]),
        board_name=str(row["board_name"]),
        category_id=int(row["category_id"]),
        category_name=str(row["category_name"]),
        title=str(row["title"]),
        body_preview=_body_preview(str(row["body"])),
        course_id=row["course_id"],
        author_id=int(row["author_id"]),
        author_username="Anonymous" if is_anon else str(row["author_username"]),
        author_role=row["author_role"],
        is_anonymous=is_anon,
        reply_count=int(row["reply_count"]),
        dylen_replied=bool(row["dylen_replied"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        latest_activity_at=str(row["latest_activity_at"]),
    )


def _reply_from_row(row: sqlite3.Row) -> ForumReplyRecord:
    is_anon = bool(row["is_anonymous"])
    return ForumReplyRecord(
        id=int(row["id"]),
        thread_id=int(row["thread_id"]),
        author_id=row["author_id"],
        author_username="Anonymous" if is_anon else str(row["author_username"]),
        author_role=row["author_role"],
        is_anonymous=is_anon,
        body=str(row["body"]),
        citations=_citations_from_json(row["citations_json"]),
        created_at=str(row["created_at"]),
    )


class ForumStore:
    """Thin sqlite3 wrapper for subject boards, threads, and replies."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def from_database_url(cls, database_url: str) -> ForumStore:
        return cls(_sqlite_path(database_url))

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forum_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    color TEXT NOT NULL,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_by_username TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forum_boards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL
                        REFERENCES forum_categories(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    created_by_username TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category_id, slug)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forum_threads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    board_id INTEGER NOT NULL REFERENCES forum_boards(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    course_id TEXT,
                    author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    author_username TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    is_anonymous INTEGER NOT NULL DEFAULT 0,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    dylen_replied INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    latest_activity_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forum_replies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id INTEGER NOT NULL REFERENCES forum_threads(id) ON DELETE CASCADE,
                    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    author_username TEXT NOT NULL,
                    author_role TEXT NOT NULL,
                    is_anonymous INTEGER NOT NULL DEFAULT 0,
                    body TEXT NOT NULL,
                    citations_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Migration: add is_anonymous to existing tables
            for stmt in (
                "ALTER TABLE forum_threads ADD COLUMN is_anonymous INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE forum_replies ADD COLUMN is_anonymous INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    connection.execute(stmt)
                except Exception:
                    pass  # column already exists
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_forum_boards_category
                ON forum_boards(category_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_forum_threads_board_activity
                ON forum_threads(board_id, latest_activity_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_forum_replies_thread
                ON forum_replies(thread_id, created_at)
                """
            )
            self._seed_defaults(connection)

    def _seed_defaults(self, connection: sqlite3.Connection) -> None:
        has_category = connection.execute("SELECT 1 FROM forum_categories LIMIT 1").fetchone()
        if has_category is not None:
            return
        timestamp = _now()
        connection.executemany(
            """
            INSERT INTO forum_categories (
                name, slug, description, color, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (name, _slug(name), description, color, timestamp, timestamp)
                for name, description, color in DEFAULT_CATEGORIES
            ],
        )

    def list_categories(self) -> list[ForumCategoryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM forum_categories
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [_category_from_row(row) for row in rows]

    def list_boards(self) -> list[ForumBoardRecord]:
        with self._connect() as connection:
            rows = connection.execute(self._board_select()).fetchall()
        return [_board_from_row(row) for row in rows]

    def get_board(self, board_id: int) -> ForumBoardRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                self._board_select("WHERE b.id = ?"),
                (board_id,),
            ).fetchone()
        return _board_from_row(row) if row is not None else None

    def create_category(
        self,
        *,
        name: str,
        description: str,
        color: str | None,
        created_by: int,
        created_by_username: str,
    ) -> ForumCategoryRecord:
        clean_name = _clean_text(name, "name", max_length=80)
        clean_description = _clean_text(description, "description", max_length=300)
        clean_color = _clean_color(color)
        slug = _slug(clean_name)
        timestamp = _now()
        with self._connect() as connection, connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO forum_categories (
                        name, slug, description, color, created_by,
                        created_by_username, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        clean_name,
                        slug,
                        clean_description,
                        clean_color,
                        created_by,
                        created_by_username,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ForumStoreError("category already exists") from exc
            row = connection.execute(
                "SELECT * FROM forum_categories WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return _category_from_row(row)

    def create_board(
        self,
        *,
        category_id: int,
        name: str,
        description: str,
        created_by: int,
        created_by_username: str,
    ) -> ForumBoardRecord:
        clean_name = _clean_text(name, "name", max_length=90)
        clean_description = _clean_text(description, "description", max_length=360)
        timestamp = _now()
        with self._connect() as connection, connection:
            category = connection.execute(
                "SELECT 1 FROM forum_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if category is None:
                raise ForumStoreError("category not found")
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO forum_boards (
                        category_id, name, slug, description, created_by,
                        created_by_username, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category_id,
                        clean_name,
                        _slug(clean_name),
                        clean_description,
                        created_by,
                        created_by_username,
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ForumStoreError("board already exists in this category") from exc
        board = self.get_board(int(cursor.lastrowid))
        if board is None:
            raise ForumStoreError("created board could not be loaded")
        return board

    def list_threads(self, *, board_id: int) -> list[ForumThreadSummaryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                f"{self._thread_summary_select()} WHERE t.board_id = ? "
                "ORDER BY t.latest_activity_at DESC, t.id DESC",
                (board_id,),
            ).fetchall()
        return [_thread_summary_from_row(row) for row in rows]

    def create_thread(
        self,
        *,
        board_id: int,
        title: str,
        body: str,
        course_id: str | None,
        author_id: int,
        author_username: str,
        author_role: ForumRole,
        anonymous: bool = False,
    ) -> ForumThreadRecord:
        clean_title = _clean_text(title, "title", max_length=140)
        clean_body = _clean_text(body, "body", max_length=8000, min_length=3)
        clean_course_id = _clean_course_id(course_id)
        timestamp = _now()
        with self._connect() as connection, connection:
            board = connection.execute(
                "SELECT 1 FROM forum_boards WHERE id = ?",
                (board_id,),
            ).fetchone()
            if board is None:
                raise ForumStoreError("board not found")
            cursor = connection.execute(
                """
                INSERT INTO forum_threads (
                    board_id, title, body, course_id, author_id, author_username,
                    author_role, is_anonymous, created_at, updated_at, latest_activity_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    board_id,
                    clean_title,
                    clean_body,
                    clean_course_id,
                    author_id,
                    author_username,
                    author_role,
                    1 if anonymous else 0,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
        thread = self.get_thread(int(cursor.lastrowid))
        if thread is None:
            raise ForumStoreError("created thread could not be loaded")
        return thread

    def get_thread(self, thread_id: int) -> ForumThreadRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                f"{self._thread_summary_select()} WHERE t.id = ?",
                (thread_id,),
            ).fetchone()
            if row is None:
                return None
            replies = [
                _reply_from_row(reply_row)
                for reply_row in connection.execute(
                    """
                    SELECT *
                    FROM forum_replies
                    WHERE thread_id = ?
                    ORDER BY created_at, id
                    """,
                    (thread_id,),
                ).fetchall()
            ]
        is_anon = bool(row["is_anonymous"])
        return ForumThreadRecord(
            id=int(row["id"]),
            board_id=int(row["board_id"]),
            board_name=str(row["board_name"]),
            category_id=int(row["category_id"]),
            category_name=str(row["category_name"]),
            title=str(row["title"]),
            body=str(row["body"]),
            course_id=row["course_id"],
            author_id=int(row["author_id"]),
            author_username="Anonymous" if is_anon else str(row["author_username"]),
            author_role=row["author_role"],
            is_anonymous=is_anon,
            reply_count=int(row["reply_count"]),
            dylen_replied=bool(row["dylen_replied"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            latest_activity_at=str(row["latest_activity_at"]),
            replies=replies,
        )

    def add_reply(
        self,
        *,
        thread_id: int,
        body: str,
        author_id: int | None,
        author_username: str,
        author_role: ForumRole,
        citations: list[Citation] | None = None,
        anonymous: bool = False,
    ) -> ForumReplyRecord:
        clean_body = _clean_text(body, "body", max_length=8000, min_length=1)
        timestamp = _now()
        with self._connect() as connection, connection:
            thread = connection.execute(
                "SELECT 1 FROM forum_threads WHERE id = ?",
                (thread_id,),
            ).fetchone()
            if thread is None:
                raise ForumStoreError("thread not found")
            cursor = connection.execute(
                """
                INSERT INTO forum_replies (
                    thread_id, author_id, author_username, author_role,
                    is_anonymous, body, citations_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    author_id,
                    author_username,
                    author_role,
                    1 if anonymous else 0,
                    clean_body,
                    _citations_to_json(citations),
                    timestamp,
                ),
            )
            dylen_replied = 1 if author_role == "bot" else 0
            connection.execute(
                """
                UPDATE forum_threads
                SET reply_count = reply_count + 1,
                    dylen_replied = CASE
                        WHEN ? = 1 THEN 1
                        ELSE dylen_replied
                    END,
                    updated_at = ?,
                    latest_activity_at = ?
                WHERE id = ?
                """,
                (dylen_replied, timestamp, timestamp, thread_id),
            )
            row = connection.execute(
                "SELECT * FROM forum_replies WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return _reply_from_row(row)

    @staticmethod
    def _board_select(where_clause: str = "") -> str:
        return f"""
            SELECT
                b.*,
                c.name AS category_name,
                COUNT(DISTINCT t.id) AS thread_count,
                COUNT(r.id) AS reply_count,
                MAX(t.latest_activity_at) AS latest_activity_at
            FROM forum_boards b
            JOIN forum_categories c ON c.id = b.category_id
            LEFT JOIN forum_threads t ON t.board_id = b.id
            LEFT JOIN forum_replies r ON r.thread_id = t.id
            {where_clause}
            GROUP BY b.id
            ORDER BY c.name COLLATE NOCASE, b.name COLLATE NOCASE
        """

    @staticmethod
    def _thread_summary_select() -> str:
        return """
            SELECT
                t.*,
                b.name AS board_name,
                c.id AS category_id,
                c.name AS category_name
            FROM forum_threads t
            JOIN forum_boards b ON b.id = t.board_id
            JOIN forum_categories c ON c.id = b.category_id
        """
