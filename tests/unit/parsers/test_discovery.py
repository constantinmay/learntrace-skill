from pathlib import Path

from learntrace.parsers import discover_static_materials


def test_discovers_documents_and_test_logs_without_reading_excluded_dirs(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "report.md").write_text("# Report\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "pytest-output.txt").write_text("1 passed\n", encoding="utf-8")
    (tmp_path / "run.log").write_text("1 passed\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".uv-cache").mkdir()
    (tmp_path / ".uv-cache" / "dependency.txt").write_text("cache\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    materials = discover_static_materials(tmp_path)

    assert materials.documents == (Path("docs/report.md"), Path("notes.txt"))
    assert materials.test_logs == (Path("pytest-output.txt"), Path("run.log"))
    assert materials.has_git is True
