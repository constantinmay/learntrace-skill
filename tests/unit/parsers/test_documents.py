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
    assert result.events[0].source_refs[0].ref == "docs/report.md:1-2"
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
    assert str(tmp_path) not in result.warnings[0].source


def test_preserves_long_markdown_content_across_chunks(tmp_path: Path) -> None:
    paragraphs = [f"第 {index} 段：" + "内容" * 120 for index in range(1, 5)]
    document = tmp_path / "long.md"
    document.write_text("# 长章节\n" + "\n\n".join(paragraphs) + "\n", encoding="utf-8")

    result = parse_documents(tmp_path, [Path("long.md")])

    assert len(result.events) > 1
    combined = " ".join(event.summary for event in result.events)
    assert all(paragraph in combined for paragraph in paragraphs)
    assert result.events[-1].source_refs[0].ref.endswith(":6-8")


def test_keeps_fenced_code_block_in_one_document_event(tmp_path: Path) -> None:
    document = tmp_path / "design.md"
    document.write_text(
        "# 示例\n\n```python\nvalue = 1\nprint(value)\n```\n",
        encoding="utf-8",
    )

    result = parse_documents(tmp_path, [Path("design.md")])

    assert len(result.events) == 1
    assert "```python value = 1 print(value) ```" in result.events[0].summary
