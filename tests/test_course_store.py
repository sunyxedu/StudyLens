from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from studylens.storage import CourseRecord, CourseStore
from studylens.storage.courses import _sqlite_path


def test_sqlite_path_accepts_sqlite_url_and_bare_path(tmp_path: Path) -> None:
    assert _sqlite_path("sqlite:///data/x.db") == Path("data/x.db")
    assert _sqlite_path(str(tmp_path / "x.db")) == tmp_path / "x.db"
    with pytest.raises(ValueError, match="Unsupported"):
        _sqlite_path("postgres://example/db")


def test_course_store_replace_all_overwrites_and_lists_in_code_order(tmp_path: Path) -> None:
    store = CourseStore(tmp_path / "studylens.db")
    assert store.list_all() == []

    store.replace_all(
        [
            ("COMP50002", "Software Engineering Design", "https://edstem.org/x/2"),
            ("COMP50001", "Algorithm Design", "https://edstem.org/x/1"),
        ]
    )

    rows = store.list_all()
    assert [r.code for r in rows] == ["COMP50001", "COMP50002"]
    assert all(isinstance(r, CourseRecord) for r in rows)
    assert rows[0].title == "Algorithm Design"
    assert rows[0].updated_at  # populated

    # A second replace removes anything not in the new list.
    store.replace_all([("COMP50001", "Algorithm Design (renamed)", None)])
    rows = store.list_all()
    assert [r.code for r in rows] == ["COMP50001"]
    assert rows[0].title == "Algorithm Design (renamed)"
    assert rows[0].edstem_url is None


def test_course_store_from_database_url_creates_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "subdir" / "studylens.db"
    store = CourseStore.from_database_url(f"sqlite:///{db_path}")
    assert store.path == db_path
    assert db_path.parent.exists()


def test_course_store_scopes_courses_by_user_id(tmp_path: Path) -> None:
    store = CourseStore(tmp_path / "studylens.db")

    store.replace_all([("COMP50001", "Algorithms", None)], user_id=1)
    store.replace_all([("COMP60001", "AI", None)], user_id=2)

    assert [r.code for r in store.list_all(user_id=1)] == ["COMP50001"]
    assert [r.code for r in store.list_all(user_id=2)] == ["COMP60001"]
    assert store.list_all() == []

    store.mark_indexed("COMP50001", user_id=1)
    assert store.list_all(user_id=1)[0].indexed_at
    assert store.list_all(user_id=2)[0].indexed_at is None


def test_course_store_migrates_legacy_global_table(tmp_path: Path) -> None:
    db_path = tmp_path / "studylens.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE courses (
                code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                edstem_url TEXT,
                updated_at TEXT NOT NULL,
                indexed_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO courses (code, title, edstem_url, updated_at, indexed_at)
            VALUES ('COMP50001', 'Algorithms', NULL, '2026-01-01T00:00:00+00:00', NULL)
            """
        )

    store = CourseStore(db_path)

    assert [r.code for r in store.list_all()] == ["COMP50001"]
    assert store.list_all(user_id=1) == []
