from pathlib import Path

from learntrace.models import ContractValidator
from learntrace.parsers import parse_documents


def test_parses_markdown_sections_with_posix_source_refs(tmp_path: Path) -> None:
    document = tmp_path / "docs" / "report.md"
    document.parent.mkdir()
    document.write_text(
        "# 项目目标\n读取课程数据。\n\n## 约束\n缺失字段必须报错。\n",
        encoding="utf-8",
    )

    result = parse_documents(tmp_path, [Path("docs/report.md")])

    assert result.warnings == ()
    assert len(result.events) == 2
    assert result.events[0].summary == "文档章节“项目目标”记录：读取课程数据。"
    assert result.events[0].source_refs[0].ref == "docs/report.md:1-3"
    assert result.events[1].source_refs[0].ref == "docs/report.md:4-5"
    validator = ContractValidator()
    assert all(validator.is_valid("observable_event", event.to_dict()) for event in result.events)


def test_parses_text_paragraphs_with_stable_ids(tmp_path: Path) -> None:
    document = tmp_path / "notes.txt"
    document.write_text("第一段。\n\n第二段。\n", encoding="utf-8")

    first = parse_documents(tmp_path, [Path("notes.txt")])
    second = parse_documents(tmp_path, [Path("notes.txt")])

    assert len(first.events) == 2
    assert [event.id for event in first.events] == [event.id for event in second.events]


def test_reports_invalid_utf8_without_failing_other_documents(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    (tmp_path / "good.md").write_text("# Good\ncontent\n", encoding="utf-8")

    result = parse_documents(tmp_path, [Path("bad.md"), Path("good.md")])

    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["invalid_utf8"]


def test_rejects_path_outside_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")

    result = parse_documents(project, [outside])

    assert result.events == ()
    assert result.warnings[0].code == "invalid_source"
