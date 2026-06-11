"""Curated course catalog for managed programs (e.g. Computing).

Demo-stage implementation: the catalog is a hardcoded constant. The public
surface (`catalog_courses_for`, `is_catalog_program`) is intentionally narrow
so a future full implementation can swap the constant for a database table
(filtered by program + year) without touching any call site.

A user whose program is served by the catalog skips the whole onboarding
flow (connect Imperial logins -> discover -> process). Instead their course
list is seeded directly from the catalog.

Which courses are "Ready" (vs "Pending") is NOT hardcoded here: at seed time
we ask the vector store whether each course_id already has content (indexed by
anyone — the store is global by course_id), and only those are marked indexed.
So processing a course with any account makes it Ready for every catalog user,
and this list never has to track processing status by hand.
"""

from __future__ import annotations

# Program label exactly as offered in the registration dropdown.
COMPUTING = "Computing"

# (course_code, course_title, edstem_url). ``course_code`` is used verbatim as
# the ``course_id`` for retrieval (after normalization), so it must equal the
# code under which content was indexed. ``edstem_url`` is display-only.
CatalogCourse = tuple[str, str, str | None]

COMPUTING_CATALOG: list[CatalogCourse] = [
    ("COMP50001", "Algorithm Design and Analysis", "https://edstem.org/us/courses/86557"),
    ("COMP50002", "Software Engineering Design", "https://edstem.org/us/courses/86558"),
    ("COMP50003", "Models of Computation", "https://edstem.org/us/courses/86559"),
    ("COMP50004", "Operating Systems", "https://edstem.org/us/courses/86560"),
    ("COMP50005", "Networks and Communications", "https://edstem.org/us/courses/86561"),
    ("COMP50007.1", "Computing Practical 2 (Lab)", "https://edstem.org/us/courses/86562"),
    ("COMP50008", "Probability and Statistics", "https://edstem.org/us/courses/86563"),
    ("COMP50010", "Designing for Real People", "https://edstem.org/us/courses/86565"),
    ("COMP50010.2", "Designing for Real People (Intro to Law)", "https://edstem.org/us/courses/86566"),
    ("COMP50011", "Computational Techniques", "https://edstem.org/us/courses/86567"),
    ("COMP50013", "Machine Learning", "https://edstem.org/us/courses/86568"),
]


def is_catalog_program(course: str | None) -> bool:
    """True if the user's program is served by a pre-indexed catalog."""
    return bool(course) and course.strip().casefold() == COMPUTING.casefold()


def catalog_courses_for(
    program: str | None,
    year: str | None = None,  # noqa: ARG001 - reserved for the table-backed version
) -> list[CatalogCourse]:
    """Return ``(code, title, edstem_url)`` triples for a managed program.

    Demo stage ignores ``year`` and returns the Computing list. The full
    version will query a catalog table filtered by program + year here,
    leaving every caller unchanged.
    """
    if is_catalog_program(program):
        return list(COMPUTING_CATALOG)
    return []
