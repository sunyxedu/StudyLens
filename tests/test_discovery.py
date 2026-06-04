import asyncio
from pathlib import Path

from studylens.api import discovery as discovery_mod
from studylens.api.discovery import BrowserStateMissingError, CourseDiscoveryManager
from studylens.config import Settings
from studylens.ingestion.edstem_agent import EdStemCourse, EdStemDiscoveryReport
from studylens.storage import AuthStore, CourseStore


def _stores(tmp_path: Path) -> tuple[AuthStore, CourseStore, Settings]:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'studylens.db'}",
        auth_secret_key="test-secret",
    )
    auth = AuthStore.from_database_url(settings.database_url, secret_key="test-secret")
    courses = CourseStore.from_database_url(settings.database_url)
    return auth, courses, settings


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeBrowserSession:
    @classmethod
    def from_storage_state(cls, _state: dict) -> _FakeSession:
        return _FakeSession()


def test_start_without_browser_state_raises(tmp_path: Path) -> None:
    auth, courses, settings = _stores(tmp_path)
    user = auth.register_user(
        username="bob", grade="Y3", course="Computing", password="correct horse battery"
    )
    manager = CourseDiscoveryManager(auth_store=auth, course_store=courses, settings=settings)

    assert manager.status(user).status == "idle"

    try:
        manager.start(user)
    except BrowserStateMissingError:
        pass
    else:  # pragma: no cover - explicit failure path
        raise AssertionError("expected BrowserStateMissingError")


def test_discovery_records_courses_and_fixes_started_at(tmp_path: Path, monkeypatch) -> None:
    auth, courses, settings = _stores(tmp_path)
    user = auth.register_user(
        username="amy", grade="Y3", course="Computing", password="correct horse battery"
    )
    auth.save_browser_state(user.id, {"cookies": [{"name": "sid", "value": "x"}]})

    async def fake_discover(_session, _settings):
        return EdStemDiscoveryReport(
            courses=[
                EdStemCourse(code="COMP50002", title="COMP 50002: SE Design"),
                EdStemCourse(code="COMP60001", title="COMP 60001: Networks"),
            ],
            dropped_titles=[],
            num_turns=3,
            total_cost_usd=0.01,
            stop_reason="end_turn",
        )

    monkeypatch.setattr(discovery_mod, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(discovery_mod, "discover_edstem_courses", fake_discover)

    manager = CourseDiscoveryManager(auth_store=auth, course_store=courses, settings=settings)

    async def scenario():
        running = manager.start(user)
        assert running.status == "running"
        assert running.started_at is not None
        await manager._jobs[user.id].task
        return running.started_at, manager.status(user)

    started_at, final = asyncio.run(scenario())
    assert final.status == "done"
    assert final.course_count == 2
    assert final.started_at == started_at  # the origin timestamp never moves
    assert {c.code for c in courses.list_all(user_id=user.id)} == {"COMP50002", "COMP60001"}


def test_discovery_reports_error_when_agent_fails(tmp_path: Path, monkeypatch) -> None:
    auth, courses, settings = _stores(tmp_path)
    user = auth.register_user(
        username="cleo", grade="Y3", course="Computing", password="correct horse battery"
    )
    auth.save_browser_state(user.id, {"cookies": [{"name": "sid", "value": "x"}]})

    async def fake_discover(_session, _settings):
        return EdStemDiscoveryReport(
            courses=[],
            dropped_titles=[],
            num_turns=1,
            total_cost_usd=0.0,
            stop_reason="error",
            error="agent blew up",
        )

    monkeypatch.setattr(discovery_mod, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(discovery_mod, "discover_edstem_courses", fake_discover)

    manager = CourseDiscoveryManager(auth_store=auth, course_store=courses, settings=settings)

    async def scenario():
        manager.start(user)
        await manager._jobs[user.id].task
        return manager.status(user)

    final = asyncio.run(scenario())
    assert final.status == "error"
    assert final.error == "agent blew up"
