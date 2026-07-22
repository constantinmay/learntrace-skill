from pathlib import Path

from learntrace.models import ContractValidator
from learntrace.parsers import PARSER_VERSION, parse_static_materials


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
