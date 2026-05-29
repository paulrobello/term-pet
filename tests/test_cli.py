"""Tests for the CLI entry point."""

from typer.testing import CliRunner

from tpet.cli import app

runner = CliRunner()


class TestCli:
    def test_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.2.0" in result.stdout

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "tpet" in result.stdout.lower()

    def test_dump_config(self, tmp_path) -> None:
        result = runner.invoke(app, ["--dump-config", "--config-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "comment_interval_seconds" in result.stdout

    def test_dry_run(self, tmp_path) -> None:
        result = runner.invoke(app, ["--dry-run", "--config-dir", str(tmp_path)])
        assert result.exit_code == 0
