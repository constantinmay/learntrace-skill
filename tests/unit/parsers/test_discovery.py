import errno
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from learntrace.parsers import discover_static_materials
from learntrace.parsers import discovery as discovery_module


class _MonkeyPatch(Protocol):
    def setattr(self, target: object, name: str, value: object) -> None: ...


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
    assert materials.warnings == ()


def test_warns_when_a_directory_cannot_be_scanned_and_continues(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    readable = tmp_path / "readable"
    readable.mkdir()
    (readable / "notes.md").write_text("# Notes\n", encoding="utf-8")
    blocked = tmp_path / "blocked"

    def walk_with_error(
        top: os.PathLike[str] | str,
        *,
        followlinks: bool,
        onerror: Callable[[OSError], object] | None,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        assert Path(top) == tmp_path.resolve()
        assert followlinks is False
        yield str(tmp_path), ["blocked", "readable"], []
        assert onerror is not None
        onerror(PermissionError(errno.EACCES, "Permission denied", str(blocked)))
        yield str(readable), [], ["notes.md"]

    monkeypatch.setattr(discovery_module.os, "walk", walk_with_error)

    materials = discover_static_materials(tmp_path)

    assert materials.documents == (Path("readable/notes.md"),)
    assert len(materials.warnings) == 1
    warning = materials.warnings[0]
    assert warning.code == "discovery_error"
    assert warning.source == "blocked"
    assert warning.message == f"Permission denied (errno {errno.EACCES})"


def test_discovery_warning_does_not_expose_an_outside_absolute_path(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    outside = tmp_path.parent / "private" / "blocked"

    def walk_with_outside_error(
        top: os.PathLike[str] | str,
        *,
        followlinks: bool,
        onerror: Callable[[OSError], object] | None,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        del top, followlinks
        assert onerror is not None
        onerror(PermissionError(errno.EACCES, "Permission denied", str(outside)))
        yield str(tmp_path), [], []

    monkeypatch.setattr(discovery_module.os, "walk", walk_with_outside_error)

    warning = discover_static_materials(tmp_path).warnings[0]

    assert warning.source == "[outside-project]/blocked"
    serialized = str(warning.to_dict())
    assert str(outside.parent) not in serialized
