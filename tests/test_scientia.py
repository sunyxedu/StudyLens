from studylens.domain import CourseSummary
from studylens.ingestion.scientia import classify_resource, infer_course_id, parse_course_page, parse_timeline


def test_parse_timeline_finds_courses_and_deduplicates() -> None:
    html = """
    <a href="/2526/modules/COMP70001">COMP70001 Advanced Algorithms</a>
    <a href="/2526/modules/COMP70001">COMP70001 Advanced Algorithms</a>
    <a href="mailto:test@example.com">Email</a>
    <a href="/about">About</a>
    """

    courses = parse_timeline(html, "https://scientia.doc.ic.ac.uk/2526/timeline")

    assert len(courses) == 1
    assert courses[0].id == "COMP70001"
    assert courses[0].url == "https://scientia.doc.ic.ac.uk/2526/modules/COMP70001"


def test_parse_course_page_classifies_materials_exercises_and_tutorials() -> None:
    html = """
    <h2>Materials</h2><a href="lecture1.pdf">Lecture 1 slides</a>
    <h2>Exercises</h2><a href="exercise1.pdf">Problem Sheet 1</a>
    <h2>Tutorials</h2><a href="tutorial1.pdf">Tutorial 1</a>
    """
    summary = CourseSummary(
        id="COMP70001",
        title="COMP70001 Advanced Algorithms",
        url="https://scientia.doc.ic.ac.uk/course",
    )

    course = parse_course_page(html, summary, "https://scientia.doc.ic.ac.uk/course")

    assert [resource.title for resource in course.materials] == ["Lecture 1 slides"]
    assert [resource.title for resource in course.exercises] == ["Problem Sheet 1"]
    assert [resource.title for resource in course.tutorials] == ["Tutorial 1"]
    assert course.materials[0].source_url == "https://scientia.doc.ic.ac.uk/lecture1.pdf"


def test_course_id_inference_and_resource_classification() -> None:
    assert infer_course_id("COMP70001 Advanced Algorithms") == "COMP70001"
    assert classify_resource("Week 2 Tutorial", "tutorial.pdf") == "tutorial"
    assert classify_resource("Problem Sheet", "sheet.pdf") == "exercise"
    assert classify_resource("Lecture notes", "notes.pdf") == "material"

