from studylens.ingestion.edstem import EdStemCrawler
from studylens.ingestion.exams import parse_all_pdfs, parse_exam_links


def test_edstem_scope_filter_matches_exam_relevance() -> None:
    posts = [
        {"title": "Exam scope", "body": "Lecture 9 will not be assessed."},
        {"title": "Social", "body": "Coffee after class."},
        {"title": "Coursework help", "body": "Deadline moved."},
    ]

    filtered = EdStemCrawler.filter_exam_scope_posts(posts)

    assert [post["title"] for post in filtered] == ["Exam scope"]


def test_parse_exam_links_root_index_only_keeps_course_specific_pdfs() -> None:
    html = """
    <a href="/COMP70001/2024-paper.pdf">COMP70001 2024 Paper</a>
    <a href="/COMP70001/2024-paper.pdf">duplicate</a>
    <a href="/random.txt">ignore</a>
    <a href="/shared/exam-2023.pdf">General exam paper</a>
    <a href="/COMP70002/2024-paper.pdf">other course</a>
    """

    resources = parse_exam_links(html, "https://exams.doc.ic.ac.uk/", "COMP70001")

    assert [r.source_url for r in resources] == [
        "https://exams.doc.ic.ac.uk/COMP70001/2024-paper.pdf",
    ]
    assert resources[0].kind == "past_exam"


def test_parse_all_pdfs_takes_every_pdf_on_a_course_page() -> None:
    html = """
    <a href="2023.pdf">2023 paper</a>
    <a href="2024.pdf">2024 paper</a>
    <a href="solutions.pdf">solutions</a>
    <a href="notes.txt">not a paper</a>
    <a href="2024.pdf">duplicate</a>
    """

    resources = parse_all_pdfs(
        html, "https://exams.doc.ic.ac.uk/COMP70001/", "COMP70001"
    )

    urls = [r.source_url for r in resources]
    assert urls == [
        "https://exams.doc.ic.ac.uk/COMP70001/2023.pdf",
        "https://exams.doc.ic.ac.uk/COMP70001/2024.pdf",
        "https://exams.doc.ic.ac.uk/COMP70001/solutions.pdf",
    ]
    assert all(r.kind == "past_exam" for r in resources)
