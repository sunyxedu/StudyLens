from studylens.ingestion.scientia import (
    derive_tab_urls,
    parse_course_tab,
)


def test_parse_course_tab_extracts_external_resource_anchors() -> None:
    _LECTURE = (
        "/external-resource?url="
        "https%3A%2F%2Fscientia.test%2Fapi%2Fresources%2F1%2Ffile%2FLecture-1.pdf"
    )
    _SHEET = (
        "/external-resource?url="
        "https%3A%2F%2Fscientia.test%2Fapi%2Fresources%2F2%2Ffile%2FSheet01.pdf"
    )
    html = f"""
    <body>
      <nav>
        <a href="/2526/timeline">timeline</a>
        <a href="/2526/modules">modules</a>
      </nav>
      <main>
        <a href="{_LECTURE}">Lecture 1.pdf file</a>
        <a href="{_SHEET}">Sheet01.pdf file</a>
        <a href="{_LECTURE}">duplicate</a>
        <a href="/2526/modules/50001/exercises">Exercises</a>
        <a href="/api/resources/zipped?year=2526&course=50001">download all</a>
      </main>
    </body>
    """

    resources = parse_course_tab(
        html,
        "https://scientia.test/2526/modules/50001/materials",
        course_id="50001",
        kind="material",
    )

    urls = [r.source_url for r in resources]
    titles = [r.title for r in resources]
    assert urls == [
        "https://scientia.test/api/resources/1/file/Lecture-1.pdf",
        "https://scientia.test/api/resources/2/file/Sheet01.pdf",
    ]
    assert titles == ["Lecture 1.pdf", "Sheet01.pdf"]
    assert all(r.kind == "material" for r in resources)
    assert all(r.course_id == "50001" for r in resources)


def test_derive_tab_urls_appends_missing_tab_suffix() -> None:
    assert derive_tab_urls("https://scientia.test/2526/modules/50001") == {
        "material": "https://scientia.test/2526/modules/50001/materials",
        "exercise": "https://scientia.test/2526/modules/50001/exercises",
        "tutorial": "https://scientia.test/2526/modules/50001/tutorials",
    }


def test_derive_tab_urls_replaces_existing_tab_suffix() -> None:
    assert derive_tab_urls("https://scientia.test/2526/modules/50001/materials") == {
        "material": "https://scientia.test/2526/modules/50001/materials",
        "exercise": "https://scientia.test/2526/modules/50001/exercises",
        "tutorial": "https://scientia.test/2526/modules/50001/tutorials",
    }
    assert derive_tab_urls("https://scientia.test/2526/modules/50001/exercises") == {
        "material": "https://scientia.test/2526/modules/50001/materials",
        "exercise": "https://scientia.test/2526/modules/50001/exercises",
        "tutorial": "https://scientia.test/2526/modules/50001/tutorials",
    }
