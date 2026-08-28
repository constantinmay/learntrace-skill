from dataclasses import replace
from pathlib import Path
from typing import Protocol

from learntrace.models import ContractValidator
from learntrace.parsers import (
    PARSER_VERSION,
    DiscoveredMaterials,
    ParseResult,
    ParseWarning,
    discover_static_materials,
    parse_static_materials,
)
from learntrace.parsers import static as static_module


class _MonkeyPatch(Protocol):
    def setattr(self, target: object, name: str, value: object) -> None: ...


def test_combines_confirmed_documents_and_logs_without_git(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("# Goal\nRead records.\n", encoding="utf-8")
    (tmp_path / "pytest.log").write_text("2 passed in 0.01s\n", encoding="utf-8")

    result = parse_static_materials(
        tmp_path,
        document_paths=[Path("report.md")],
        test_log_paths=[Path("pytest.log")],
        include_git=False,
    )

    assert len(result.events) == 2
    assert result.warnings == ()
    assert result.scope is not None
    assert result.scope.include_git is False
    assert result.scope.documents == ("report.md",)
    assert result.inventory is not None
    assert result.inventory.documents == ("report.md",)
    validator = ContractValidator()
    assert all(validator.is_valid("observable_event", event.to_dict()) for event in result.events)
    assert result.to_dict()["parser_version"] == PARSER_VERSION


def test_deduplicates_equivalent_confirmed_paths(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one paragraph\n", encoding="utf-8")

    result = parse_static_materials(
        tmp_path,
        document_paths=[Path("notes.txt"), tmp_path / "notes.txt"],
        test_log_paths=[],
        include_git=False,
    )

    assert len(result.events) == 1
    assert result.scope is not None
    assert result.scope.documents == ("notes.txt",)


def test_preserves_discovery_warnings_in_parse_result(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    discovered = discover_static_materials(tmp_path)
    warning = ParseWarning(
        "discovery_error",
        "blocked",
        "Permission denied (errno 13)",
    )

    def discover_with_warning(root: Path) -> DiscoveredMaterials:
        assert root == tmp_path.resolve()
        return replace(discovered, warnings=(warning,))

    monkeypatch.setattr(static_module, "discover_static_materials", discover_with_warning)

    result = parse_static_materials(
        tmp_path,
        document_paths=[],
        test_log_paths=[],
        include_git=False,
    )

    assert result.warnings == (warning,)
    assert result.to_dict()["warnings"] == [warning.to_dict()]


def test_serializes_git_author_scope_only_when_set(tmp_path: Path) -> None:
    (tmp_path / "report.md").write_text("# Goal\nRead records.\n", encoding="utf-8")

    no_author = parse_static_materials(
        tmp_path,
        document_paths=[Path("report.md")],
        test_log_paths=[],
        include_git=True,
    )

    assert no_author.scope is not None
    assert no_author.scope.git_author is None
    assert "git_author" not in no_author.to_dict()["analysis_scope"]

    scoped = parse_static_materials(
        tmp_path,
        document_paths=[Path("report.md")],
        test_log_paths=[],
        include_git=True,
        git_author="Student One",
    )

    assert scoped.scope is not None
    assert scoped.scope.git_author == "Student One"
    assert scoped.to_dict()["analysis_scope"]["git_author"] == "Student One"


def test_forwards_expensive_copy_detection_option(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    received: list[tuple[int, bool, str | None]] = []

    def parse_git(
        root: Path,
        *,
        max_commits: int,
        find_copies_harder: bool,
        author: str | None,
    ) -> ParseResult:
        assert root == tmp_path.resolve()
        received.append((max_commits, find_copies_harder, author))
        return ParseResult()

    monkeypatch.setattr(static_module, "parse_git_history", parse_git)

    parse_static_materials(
        tmp_path,
        document_paths=[],
        test_log_paths=[],
        include_git=True,
        max_commits=7,
        find_copies_harder=True,
    )

    assert received == [(7, True, None)]
