from pathlib import Path

from studylens.config import Settings


def test_settings_split_allowed_origins_and_directories(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        qdrant_path=tmp_path / "data" / "vector" / "qdrant",
        vector_db_path=tmp_path / "data" / "vector" / "fallback.sqlite3",
        allowed_origins="http://localhost:5173, chrome-extension://abc",
    )

    settings.ensure_directories()

    assert settings.vector_store == "qdrant"
    assert settings.allowed_origins == ["http://localhost:5173", "chrome-extension://abc"]
    assert settings.raw_dir.exists()
    assert settings.processed_dir.exists()
    assert settings.qdrant_path.parent.exists()

