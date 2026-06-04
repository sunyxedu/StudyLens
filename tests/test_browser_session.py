from __future__ import annotations

from pathlib import Path

import pytest

from studylens.api.browser_state import DEFAULT_BROWSER_STATE_STEPS, BrowserStateRouter
from studylens.config import Settings
from studylens.errors import ConfigurationError
from studylens.ingestion.browser_session import BrowserSession
from studylens.tools.browser_state import _http_credentials_from_settings


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


def test_browser_state_cli_prompts_for_missing_imperial_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        imperial_username="abc123",
        imperial_password=None,
    )
    monkeypatch.setattr(
        "studylens.tools.browser_state.getpass.getpass",
        lambda prompt: "secret",
    )

    assert _http_credentials_from_settings(settings, prompt=True) == {
        "username": "abc123",
        "password": "secret",
    }


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
