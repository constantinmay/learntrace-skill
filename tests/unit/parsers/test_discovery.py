from pathlib import Path

from learntrace.parsers import discover_static_materials


def test_discovers_documents_and_test_logs_without_reading_excluded_dirs(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "report.md").write_text("# Report\n", encoding="utf-8")
    (tmp_path / "docs" / "design.md").write_text("# Design\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_parser.py").write_text("def test_ok(): ...\n", encoding="utf-8")
    (tmp_path / "pytest-output.txt").write_text("1 passed\n", encoding="utf-8")
    (tmp_path / "run.log").write_text("1 passed\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / ".uv-cache").mkdir()
    (tmp_path / ".uv-cache" / "dependency.txt").write_text("cache\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    materials = discover_static_materials(tmp_path)

    assert materials.documents == (
        Path("docs/design.md"),
        Path("docs/report.md"),
        Path("notes.txt"),
    )
    assert materials.test_logs == (Path("pytest-output.txt"), Path("run.log"))
    assert materials.has_git is True
    assert materials.inventory.source_files == (
        "src/parser.py",
        "tests/test_parser.py",
    )
    assert materials.inventory.test_files == ("tests/test_parser.py",)
    assert materials.inventory.design_documents == ("docs/design.md",)
    assert materials.inventory.report_documents == ("docs/report.md",)
    assert dict(materials.inventory.extension_counts)[".py"] == 2
