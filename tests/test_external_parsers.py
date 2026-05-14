from studylens.ingestion.edstem import EdStemCrawler
from studylens.ingestion.exams import parse_exam_links


def test_edstem_scope_filter_matches_exam_relevance() -> None:
    posts = [
        {"title": "Exam scope", "body": "Lecture 9 will not be assessed."},
        {"title": "Social", "body": "Coffee after class."},
        {"title": "Coursework help", "body": "Deadline moved."},
    ]

    filtered = EdStemCrawler.filter_exam_scope_posts(posts)

    assert [post["title"] for post in filtered] == ["Exam scope"]


def test_parse_exam_links_keeps_papers_and_deduplicates() -> None:
    html = """
    <a href="/COMP70001/2024-paper.pdf">COMP70001 2024 Paper</a>
    <a href="/COMP70001/2024-paper.pdf">duplicate</a>
    <a href="/random.txt">ignore</a>
    <a href="/shared/exam-2023.pdf">General exam paper</a>
    """

    resources = parse_exam_links(html, "https://exams.doc.ic.ac.uk/", "COMP70001")

    assert len(resources) == 2
    assert resources[0].kind == "past_exam"
    assert resources[0].source_url == "https://exams.doc.ic.ac.uk/COMP70001/2024-paper.pdf"

