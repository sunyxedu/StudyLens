from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from studylens.api.browser_state import DEFAULT_BROWSER_STATE_STEPS, BrowserStateRouter
from studylens.config import Settings
from studylens.errors import ConfigurationError
from studylens.ingestion.browser_session import BrowserSession
from studylens.tools.browser_state import _apply_basic_auth_header, _prompt_imperial_credentials


def test_default_browser_state_steps_include_exams_site() -> None:
    exams_steps = [step for step in DEFAULT_BROWSER_STATE_STEPS if step.key == "exams"]

    assert len(exams_steps) == 1
    assert exams_steps[0].url == "https://exams.doc.ic.ac.uk/"


def test_browser_state_router_uses_imperial_credentials_for_http_auth(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        imperial_username="abc123",
        imperial_password="secret",
    )
    router = BrowserStateRouter(settings)

    assert router.http_credentials() == {
        "username": "abc123",
        "password": "secret",
    }


def test_browser_state_cli_prompts_for_imperial_username_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "abc123")
    monkeypatch.setattr(
        "studylens.tools.browser_state.getpass.getpass",
        lambda prompt: "secret",
    )

    assert _prompt_imperial_credentials() == {
        "username": "abc123",
        "password": "secret",
    }


def test_basic_auth_header_is_encoded_for_exams_step() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
            self.headers = headers

    page = FakePage()

    asyncio.run(
        _apply_basic_auth_header(
            page,
            {"username": "abc123", "password": "secret"},
        )
    )

    assert page.headers == {"Authorization": "Basic YWJjMTIzOnNlY3JldA=="}


def test_from_settings_requires_storage_state_path(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", browser_storage_state=None)

    with pytest.raises(ConfigurationError, match="BROWSER_STORAGE_STATE"):
        BrowserSession.from_settings(settings)


def test_from_settings_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.json"
    settings = Settings(data_dir=tmp_path / "data", browser_storage_state=missing)

    with pytest.raises(ConfigurationError, match="Browser storage state not found"):
        BrowserSession.from_settings(settings)


def test_from_settings_accepts_existing_file(tmp_path: Path) -> None:
    storage = tmp_path / "auth.json"
    storage.write_text("{}", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", browser_storage_state=storage)

    session = BrowserSession.from_settings(settings)

    assert isinstance(session, BrowserSession)


def test_from_storage_state_accepts_playwright_state_dict() -> None:
    session = BrowserSession.from_storage_state({"cookies": [], "origins": []})

    assert isinstance(session, BrowserSession)
