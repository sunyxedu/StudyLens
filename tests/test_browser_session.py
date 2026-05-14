from __future__ import annotations

from pathlib import Path

import pytest

from studylens.config import Settings
from studylens.errors import ConfigurationError
from studylens.ingestion.browser_session import BrowserSession


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
