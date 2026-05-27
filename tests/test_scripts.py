"""Tests for the CI/CD helper scripts located in .github/scripts/.

Each script is loaded at module level via importlib so tests exercise the real
code path without adding the scripts directory to sys.path permanently.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / ".github" / "scripts"


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_validate = _load_script("validate_version")
_write_version = _load_script("write_version_output")
_release_notes = _load_script("build_release_notes")

_BASE_TOML = '[project]\nversion = "1.0.0"\n'
_HEAD_TOML = '[project]\nversion = "1.1.0"\n'


class TestValidateVersion:
    """Tests for validate_version.py — semver increment and tag-existence checks."""

    def test_valid_increment_passes(self, tmp_path, monkeypatch, capsys):
        """Prints a success message when the version is properly incremented and the tag is new."""
        (tmp_path / "base.toml").write_text(_BASE_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            _validate.main()
        assert "1.0.0 -> 1.1.0" in capsys.readouterr().out

    def test_no_increment_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the PR version equals the base version."""
        (tmp_path / "base.toml").write_text(_HEAD_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with pytest.raises(SystemExit, match="must be incremented"):
            _validate.main()

    def test_version_lower_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the PR version is lower than the base version."""
        (tmp_path / "base.toml").write_text(_HEAD_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_BASE_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with pytest.raises(SystemExit, match="must be incremented"):
            _validate.main()

    def test_invalid_semver_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the version string does not match X.Y.Z format."""
        (tmp_path / "base.toml").write_text(_BASE_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.1"\n', encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with pytest.raises(SystemExit, match="Invalid version"):
            _validate.main()

    def test_tag_exists_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the target git tag already exists in the repository."""
        (tmp_path / "base.toml").write_text(_BASE_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with pytest.raises(SystemExit, match="already exists"):
                _validate.main()

    def test_missing_version_in_current_exits(self, tmp_path, monkeypatch):
        """Exits with an error when pyproject.toml in the PR branch has no version field."""
        (tmp_path / "base.toml").write_text(_BASE_TOML, encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with pytest.raises(SystemExit, match="Missing"):
            _validate.main()

    def test_missing_version_in_base_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the base branch pyproject.toml has no version field."""
        (tmp_path / "base.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "argv", ["v.py", str(tmp_path / "base.toml")])
        with pytest.raises(SystemExit, match="Missing"):
            _validate.main()

    def test_missing_args_exits(self, monkeypatch):
        """Exits with a usage hint when the base toml path argument is omitted."""
        monkeypatch.setattr(sys, "argv", ["v.py"])
        with pytest.raises(SystemExit, match="Usage"):
            _validate.main()


class TestWriteVersionOutput:
    """Tests for write_version_output.py — version extraction and GITHUB_OUTPUT writing."""

    def test_writes_version_to_output(self, tmp_path, monkeypatch, capsys):
        """Appends ``version=X.Y.Z`` to the GITHUB_OUTPUT file and prints the version."""
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        out_file = tmp_path / "github_output.env"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        _write_version.main()
        assert "version=1.1.0" in out_file.read_text(encoding="utf-8")
        assert "1.1.0" in capsys.readouterr().out

    def test_missing_version_exits(self, tmp_path, monkeypatch):
        """Exits with an error when pyproject.toml contains no version field."""
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.env"))
        with pytest.raises(SystemExit, match="Missing"):
            _write_version.main()

    def test_missing_github_output_exits(self, tmp_path, monkeypatch):
        """Exits with an error when the GITHUB_OUTPUT environment variable is not set."""
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        with pytest.raises(SystemExit, match="GITHUB_OUTPUT"):
            _write_version.main()

    def test_appends_to_existing_output_file(self, tmp_path, monkeypatch):
        """Appends to an existing GITHUB_OUTPUT file without overwriting previous entries."""
        (tmp_path / "pyproject.toml").write_text(_HEAD_TOML, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        out_file = tmp_path / "github_output.env"
        out_file.write_text("previous=value\n", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        _write_version.main()
        content = out_file.read_text(encoding="utf-8")
        assert "previous=value" in content
        assert "version=1.1.0" in content


class TestBuildReleaseNotes:
    """Tests for build_release_notes.py — Markdown release body generation."""

    def test_writes_markdown_file(self, tmp_path, monkeypatch):
        """Writes a well-formed Markdown file with PR number, body, version, and image."""
        out = tmp_path / "release.md"
        monkeypatch.setenv("PR_NUMBER", "42")
        monkeypatch.setenv("PR_TITLE", "Fix critical bug in scheduler")
        monkeypatch.setenv("PR_BODY", "Detailed description of the fix.")
        monkeypatch.setenv("VERSION", "1.1.0")
        monkeypatch.setenv("IMAGE", "ghcr.io/owner/repo")
        monkeypatch.setenv("OUTPUT_FILE", str(out))
        _release_notes.main()
        content = out.read_text(encoding="utf-8")
        assert "## #42 Fix critical bug in scheduler" in content
        assert "Detailed description of the fix." in content
        assert "`1.1.0`" in content
        assert "`ghcr.io/owner/repo:1.1.0`" in content

    def test_empty_body_uses_fallback(self, tmp_path, monkeypatch):
        """Uses a default fallback message when PR_BODY is not set."""
        out = tmp_path / "release.md"
        monkeypatch.delenv("PR_BODY", raising=False)
        monkeypatch.setenv("OUTPUT_FILE", str(out))
        _release_notes.main()
        assert "Sin resumen en la PR." in out.read_text(encoding="utf-8")

    def test_whitespace_body_uses_fallback(self, tmp_path, monkeypatch):
        """Uses a default fallback message when PR_BODY contains only whitespace."""
        out = tmp_path / "release.md"
        monkeypatch.setenv("PR_BODY", "   ")
        monkeypatch.setenv("OUTPUT_FILE", str(out))
        _release_notes.main()
        assert "Sin resumen en la PR." in out.read_text(encoding="utf-8")

    def test_multiline_body_preserved(self, tmp_path, monkeypatch):
        """Preserves newlines and list items in a multiline PR body."""
        out = tmp_path / "release.md"
        body = "Line one\n\nLine two\n- item"
        monkeypatch.setenv("PR_BODY", body)
        monkeypatch.setenv("OUTPUT_FILE", str(out))
        _release_notes.main()
        assert "Line one" in out.read_text(encoding="utf-8")
        assert "Line two" in out.read_text(encoding="utf-8")

    def test_default_output_path(self, monkeypatch):
        """Writes to /tmp/release_body.md when OUTPUT_FILE is not set."""
        monkeypatch.delenv("OUTPUT_FILE", raising=False)
        monkeypatch.delenv("PR_BODY", raising=False)
        _release_notes.main()
        assert Path("/tmp/release_body.md").exists()
