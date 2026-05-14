from typer.testing import CliRunner

from studylens.cli import app


def test_cli_auto_index_command_is_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["auto-index", "--help"])

    assert result.exit_code == 0
    assert "Scientia" in result.output


def test_cli_index_exams_command_is_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["index-exams", "--help"])

    assert result.exit_code == 0


def test_cli_index_edstem_command_is_registered() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["index-edstem", "--help"])

    assert result.exit_code == 0
