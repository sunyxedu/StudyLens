from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from studylens.config import Settings
from studylens.ingestion.browser_session import BrowserSession
from studylens.ingestion.edstem_agent import discover_edstem_courses
from studylens.storage import AuthStore, CourseStore, UserRecord


class BrowserStateMissingError(RuntimeError):
    """Raised when discovery is requested before browser logins are uploaded."""


@dataclass
class _DiscoveryJob:
    started_at: float
    status: str = "running"  # running | done | error
    finished_at: float | None = None
    error: str | None = None
    course_count: int = 0
    dropped_titles: list[str] = field(default_factory=list)
    task: asyncio.Task | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryStatus:
    status: str  # idle | running | done | error
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    course_count: int = 0


class CourseDiscoveryManager:
    """Run EdStem course discovery in the background, one job per user.

    The job's started_at is fixed at launch and reported to the frontend so the
    progress bar reads identically across refreshes — every client computes the
    same percentage from the same origin timestamp.
    """

    def __init__(
        self,
        *,
        auth_store: AuthStore,
        course_store: CourseStore,
        settings: Settings,
    ) -> None:
        self._auth_store = auth_store
        self._course_store = course_store
        self._settings = settings
        self._jobs: dict[int, _DiscoveryJob] = {}

    def status(self, user: UserRecord) -> DiscoveryStatus:
        job = self._jobs.get(user.id)
        if job is None:
            return DiscoveryStatus(status="idle")
        return DiscoveryStatus(
            status=job.status,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error=job.error,
            course_count=job.course_count,
        )

    def start(self, user: UserRecord) -> DiscoveryStatus:
        job = self._jobs.get(user.id)
        if job is not None and job.status == "running":
            return self.status(user)
        if self._auth_store.get_browser_state(user.id) is None:
            raise BrowserStateMissingError("browser state setup required")
        new_job = _DiscoveryJob(started_at=time.time())
        self._jobs[user.id] = new_job
        new_job.task = asyncio.create_task(self._run(user.id))
        return self.status(user)

    async def _run(self, user_id: int) -> None:
        job = self._jobs[user_id]
        try:
            storage_state = self._auth_store.get_browser_state(user_id)
            if storage_state is None:
                raise BrowserStateMissingError("browser state setup required")
            async with BrowserSession.from_storage_state(storage_state) as session:
                report = await discover_edstem_courses(session, self._settings)
            if report.courses:
                stored = self._course_store.replace_all(
                    ((c.code, c.title, c.edstem_url) for c in report.courses),
                    user_id=user_id,
                )
                job.course_count = len(stored)
            else:
                job.course_count = len(self._course_store.list_all(user_id=user_id))
            job.dropped_titles = list(report.dropped_titles)
            job.error = report.error
            job.status = "error" if (report.error and job.course_count == 0) else "done"
        except Exception as exc:  # pragma: no cover - browser / SDK failures.
            job.error = str(exc)
            job.status = "error"
        finally:
            job.finished_at = time.time()
