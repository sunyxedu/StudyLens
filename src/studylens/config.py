from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "local"
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/studylens.db"
    vector_store: Literal["qdrant", "sqlite"] = "qdrant"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_path: Path = Path("data/vector/qdrant")
    qdrant_collection: str = "studylens_chunks"
    vector_db_path: Path = Path("data/vector/studylens.sqlite3")

    openai_api_key: str | None = None
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4.1-mini"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Shared by every Claude Agent SDK loop (Panopto folder navigation,
    # EdStem course discovery, anything we add later).
    agent_model: str = "claude-sonnet-4-6"
    agent_max_turns: int = 80

    imperial_username: str | None = None
    imperial_password: str | None = None
    browser_storage_state: Path | None = None
    admin_token: str | None = None

    # /2526/modules is the "all my enrolled modules" list. /2526/timeline is the
    # filtered "recent activity" feed and doesn't list courses without current
    # deadlines, so it can't be the discovery entry point.
    scientia_base_url: AnyHttpUrl = "https://scientia.doc.ic.ac.uk/2526/modules"
    panopto_base_url: AnyHttpUrl = (
        "https://imperial.cloud.panopto.eu/Panopto/Pages/Sessions/List.aspx"
    )
    edstem_base_url: AnyHttpUrl = "https://edstem.org/us/dashboard"
    exams_base_url: AnyHttpUrl = "https://exams.doc.ic.ac.uk/"

    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.vector_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.qdrant_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
