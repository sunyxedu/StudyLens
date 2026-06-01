from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from studylens.storage.auth import AuthStore, AuthStoreError, verify_password


def test_auth_store_hashes_passwords_and_authenticates(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")

    result = store.authenticate_or_create(
        username=" Alice ",
        grade="Year 3",
        course="Computing",
        password="correct horse battery staple",
    )

    assert result.created is True
    assert result.user.username == "Alice"
    assert result.user.grade == "Year 3"

    with sqlite3.connect(store.path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id = ?",
            (result.user.id,),
        ).fetchone()[0]

    assert "correct horse" not in password_hash
    assert verify_password("correct horse battery staple", password_hash)

    second = store.authenticate_or_create(
        username="alice",
        grade="Year 4",
        course="Computing",
        password="correct horse battery staple",
    )

    assert second.created is False
    assert second.user.id == result.user.id
    assert second.user.grade == "Year 4"


def test_auth_store_register_and_login_are_separate(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")

    with pytest.raises(AuthStoreError, match="invalid username or password"):
        store.authenticate_user(username="alice", password="correct horse battery staple")

    registered = store.register_user(
        username="Alice",
        grade="Year 3",
        course="Computing",
        password="correct horse battery staple",
    )
    logged_in = store.authenticate_user(
        username="alice",
        password="correct horse battery staple",
    )

    assert logged_in == registered
    with pytest.raises(AuthStoreError, match="already registered"):
        store.register_user(
            username="ALICE",
            grade="Year 3",
            course="Computing",
            password="correct horse battery staple",
        )


def test_auth_store_rejects_wrong_password(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")
    store.authenticate_or_create(
        username="alice",
        grade="Year 3",
        course="Computing",
        password="correct horse battery staple",
    )

    with pytest.raises(AuthStoreError, match="invalid username or password"):
        store.authenticate_or_create(
            username="alice",
            grade="Year 3",
            course="Computing",
            password="wrong password",
        )


def test_auth_store_sessions_expire_and_revoke(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")
    user = store.authenticate_or_create(
        username="alice",
        grade="Year 3",
        course="Computing",
        password="correct horse battery staple",
    ).user

    active = store.create_session(user.id)
    expired = store.create_session(user.id, ttl=timedelta(seconds=-1))

    assert store.user_for_session(active.token) == user
    assert store.user_for_session(expired.token) is None

    store.revoke_session(active.token)
    assert store.user_for_session(active.token) is None


def test_auth_store_encrypts_browser_state(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")
    user = store.authenticate_or_create(
        username="alice",
        grade="Year 3",
        course="Computing",
        password="correct horse battery staple",
    ).user
    state = {
        "cookies": [
            {
                "name": "session",
                "value": "very-secret-cookie",
                "domain": "example.test",
                "path": "/",
            }
        ],
        "origins": [],
    }

    store.save_browser_state(user.id, state)

    assert store.has_browser_state(user.id)
    assert store.get_browser_state(user.id) == state
    assert b"very-secret-cookie" not in store.path.read_bytes()


def test_auth_store_requires_non_empty_profile_fields(tmp_path) -> None:
    store = AuthStore(tmp_path / "studylens.db", secret_key="test-secret")

    with pytest.raises(AuthStoreError, match="grade is required"):
        store.authenticate_or_create(
            username="alice",
            grade="",
            course="Computing",
            password="correct horse battery staple",
        )

    with pytest.raises(AuthStoreError, match="password must be at least 8"):
        store.authenticate_or_create(
            username="alice",
            grade="Year 3",
            course="Computing",
            password="short",
        )
