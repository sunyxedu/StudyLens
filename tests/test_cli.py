from pathlib import Path

from typer.testing import CliRunner

from studylens.cli import app


def test_cli_inspect_scientia_outputs_course_json(tmp_path: Path) -> None:
    html = tmp_path / "timeline.html"
    html.write_text(
        '<a href="/2526/modules/COMP70001">COMP70001 Advanced Algorithms</a>',
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(app, ["inspect-scientia", str(html)])

    assert result.exit_code == 0
    assert "COMP70001" in result.output
    assert "Advanced Algorithms" in result.output

