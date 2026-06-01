"""Authentication and per-user browser-state persistence.

The application stores two kinds of sensitive data:

* StudyLens login passwords, which are one-way PBKDF2 hashes.
* Playwright storage state, which must be readable later and is therefore
  encrypted at rest with an application secret.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from studylens.storage.courses import _sqlite_path

PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 390_000
SESSION_TOKEN_BYTES = 32


class AuthStoreError(ValueError):
    """Raised when auth inputs or encrypted records are invalid."""


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    grade: str
    course: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: UserRecord
    created: bool


@dataclass(frozen=True, slots=True)
class SessionRecord:
    token: str
    expires_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not normalized:
        raise AuthStoreError("username is required")
    if len(normalized) > 128:
        raise AuthStoreError("username is too long")
    return normalized


def _clean_profile_field(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AuthStoreError(f"{name} is required")
    if len(cleaned) > 128:
        raise AuthStoreError(f"{name} is too long")
    return cleaned


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise AuthStoreError("password must be at least 8 characters")
    if len(password) > 1024:
        raise AuthStoreError("password is too long")


def hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def fernet_key_from_secret(secret: str) -> bytes:
    if not secret.strip():
        raise AuthStoreError("auth secret key is required")
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def load_or_create_local_secret(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(48)
    path.write_text(secret, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return secret


def _user_from_row(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=int(row["id"]),
        username=str(row["username"]),
        grade=str(row["grade"]),
        course=str(row["course"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class AuthStore:
    """SQLite-backed users, sessions, and encrypted browser state."""

    def __init__(self, db_path: Path, *, secret_key: str) -> None:
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(fernet_key_from_secret(secret_key))
        self._initialize()

    @classmethod
    def from_database_url(cls, database_url: str, *, secret_key: str) -> AuthStore:
        return cls(_sqlite_path(database_url), secret_key=secret_key)

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
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    username_norm TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    course TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                ON sessions(user_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_states (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    encrypted_state BLOB NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def authenticate_or_create(
        self,
        *,
        username: str,
        grade: str,
        course: str,
        password: str,
    ) -> LoginResult:
        username_norm = _normalize_username(username)
        display_username = username.strip()
        clean_grade = _clean_profile_field(grade, "grade")
        clean_course = _clean_profile_field(course, "course")
        _validate_password(password)
        timestamp = _now()

        with self._connect() as connection, connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_norm = ?",
                (username_norm,),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        username, username_norm, password_hash, grade, course,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        display_username,
                        username_norm,
                        hash_password(password),
                        clean_grade,
                        clean_course,
                        timestamp,
                        timestamp,
                    ),
                )
                user_id = int(cursor.lastrowid)
                created_row = connection.execute(
                    "SELECT * FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                return LoginResult(user=_user_from_row(created_row), created=True)

            if not verify_password(password, str(row["password_hash"])):
                raise AuthStoreError("invalid username or password")

            if row["grade"] != clean_grade or row["course"] != clean_course:
                connection.execute(
                    """
                    UPDATE users
                    SET grade = ?, course = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_grade, clean_course, timestamp, row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM users WHERE id = ?",
                    (row["id"],),
                ).fetchone()
            return LoginResult(user=_user_from_row(row), created=False)

    def register_user(
        self,
        *,
        username: str,
        grade: str,
        course: str,
        password: str,
    ) -> UserRecord:
        username_norm = _normalize_username(username)
        display_username = username.strip()
        clean_grade = _clean_profile_field(grade, "grade")
        clean_course = _clean_profile_field(course, "course")
        _validate_password(password)
        timestamp = _now()

        with self._connect() as connection, connection:
            existing = connection.execute(
                "SELECT 1 FROM users WHERE username_norm = ?",
                (username_norm,),
            ).fetchone()
            if existing is not None:
                raise AuthStoreError("username is already registered")
            cursor = connection.execute(
                """
                INSERT INTO users (
                    username, username_norm, password_hash, grade, course,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    display_username,
                    username_norm,
                    hash_password(password),
                    clean_grade,
                    clean_course,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return _user_from_row(row)

    def authenticate_user(self, *, username: str, password: str) -> UserRecord:
        username_norm = _normalize_username(username)
        _validate_password(password)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_norm = ?",
                (username_norm,),
            ).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            raise AuthStoreError("invalid username or password")
        return _user_from_row(row)

    def create_session(
        self,
        user_id: int,
        *,
        ttl: timedelta = timedelta(days=14),
    ) -> SessionRecord:
        token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
        token_hash = hash_session_token(token)
        created_at = _now()
        expires_at = (datetime.now(UTC) + ttl).isoformat(timespec="seconds")
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO sessions (token_hash, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, user_id, created_at, expires_at),
            )
        return SessionRecord(token=token, expires_at=expires_at)

    def user_for_session(self, token: str | None) -> UserRecord | None:
        if not token:
            return None
        token_hash = hash_session_token(token)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.*
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > ?
                """,
                (token_hash, _now()),
            ).fetchone()
        return _user_from_row(row) if row is not None else None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection, connection:
            connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (_now(), hash_session_token(token)),
            )

    def save_browser_state(self, user_id: int, state: dict[str, Any]) -> None:
        payload = json.dumps(state, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        timestamp = _now()
        with self._connect() as connection, connection:
            connection.execute(
                """
                INSERT INTO browser_states (user_id, encrypted_state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    encrypted_state = excluded.encrypted_state,
                    updated_at = excluded.updated_at
                """,
                (user_id, encrypted, timestamp),
            )

    def get_browser_state(self, user_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT encrypted_state FROM browser_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = self._fernet.decrypt(bytes(row["encrypted_state"]))
            decoded = json.loads(payload.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError) as exc:
            raise AuthStoreError("stored browser state cannot be decrypted") from exc
        if not isinstance(decoded, dict):
            raise AuthStoreError("stored browser state is invalid")
        return decoded

    def has_browser_state(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM browser_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return row is not None

    def delete_browser_state(self, user_id: int) -> None:
        with self._connect() as connection, connection:
            connection.execute("DELETE FROM browser_states WHERE user_id = ?", (user_id,))
