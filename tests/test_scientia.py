from studylens.domain import CourseSummary
from studylens.ingestion.scientia import classify_resource, parse_course_page


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


def test_classify_resource_distinguishes_kinds() -> None:
    assert classify_resource("Week 2 Tutorial", "tutorial.pdf") == "tutorial"
    assert classify_resource("Problem Sheet", "sheet.pdf") == "exercise"
    assert classify_resource("Lecture notes", "notes.pdf") == "material"
