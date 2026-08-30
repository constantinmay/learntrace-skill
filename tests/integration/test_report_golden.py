"""Static presentation-contract checks for report-golden fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "report-golden"
SAMPLES = ("book-manager", "province-economy")
EVENT_ID_RE = re.compile(r"evt-(?:git|doc|test|trace)-[0-9a-f]+")


def _load_payload(sample: str) -> dict[str, Any]:
    path = GOLDEN_DIR / sample / "narrative-payload.golden.json"
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    return cast(dict[str, Any], raw)


def _load_report(sample: str, submitted: bool) -> str:
    name = "learning-record.submitted.md" if submitted else "learning-record.md"
    return (GOLDEN_DIR / sample / name).read_text(encoding="utf-8")


def _citation_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()

    def add(citation: Any) -> None:
        if isinstance(citation, str):
            ids.add(citation)
        elif isinstance(citation, dict):
            citation_dict = cast(dict[str, Any], citation)
            event_id = citation_dict.get("event_id")
            if isinstance(event_id, str):
                ids.add(event_id)

    for citation in cast(list[Any], payload["overview"]["citations"]):
        add(citation)
    for stage in cast(list[dict[str, Any]], payload["stages"]):
        for citation in cast(list[Any], stage["citations"]):
            add(citation)
        anchor = stage.get("merge_anchor")
        if isinstance(anchor, str):
            ids.add(anchor)
        elif isinstance(anchor, list):
            anchor_items = cast(list[Any], anchor)
            ids.update(item for item in anchor_items if isinstance(item, str))
        for change in cast(list[Any], stage["key_changes"]):
            if isinstance(change, dict):
                change_dict = cast(dict[str, Any], change)
                for citation in cast(list[Any], change_dict.get("citations", [])):
                    add(citation)
    for turning_point in cast(list[dict[str, Any]], payload["turning_points"]):
        for citation in cast(list[Any], turning_point["citations"]):
            add(citation)
    ai = cast(dict[str, Any], payload["ai_collaboration"])
    items = [
        *cast(list[dict[str, Any]], ai["observed_touchpoints"]),
        *cast(list[dict[str, Any]], ai["work_segments"]),
    ]
    for item in items:
        for citation in cast(list[Any], item["citations"]):
            add(citation)
    return ids


def _footnote_ids(markdown: str) -> set[str]:
    footnotes = "\n".join(line for line in markdown.splitlines() if line.startswith("[^"))
    return set(EVENT_ID_RE.findall(footnotes))


def _section(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    start = markdown.index(marker)
    next_heading = re.search(r"^## ", markdown[start + len(marker) :], re.MULTILINE)
    if next_heading is None:
        end = markdown.index("\n---", start)
    else:
        end = start + len(marker) + next_heading.start()
    return markdown[start:end]


@pytest.mark.parametrize("sample", SAMPLES)
def test_payload_uses_separate_touchpoints_and_work_segments(sample: str) -> None:
    payload = _load_payload(sample)
    ai = payload["ai_collaboration"]
    assert "episodes" not in ai
    assert ai["observed_touchpoints"]
    assert 2 <= len(ai["work_segments"]) <= 3

    for touchpoint in ai["observed_touchpoints"]:
        assert touchpoint["touchpoint_id"]
        assert touchpoint["title"]
        assert touchpoint["body"]
        assert touchpoint["citations"]
        assert touchpoint["derived"] is False

    for segment in ai["work_segments"]:
        assert segment["segment_id"]
        assert segment["title"]
        assert segment["summary"]
        assert segment["coverage"]
        assert segment["activity_shape"]
        assert segment["file_focus"]
        assert segment["command_categories"]
        assert segment["citations"]
        assert segment["derived"] is True
        assert segment["derivation"] == "deterministic_trace_grouping"
        assert segment["deniable"] is True


@pytest.mark.parametrize("sample", SAMPLES)
@pytest.mark.parametrize("submitted", (False, True))
def test_payload_citations_are_reachable_from_report_footnotes(
    sample: str, submitted: bool
) -> None:
    payload = _load_payload(sample)
    markdown = _load_report(sample, submitted=submitted)
    assert _citation_ids(payload) <= _footnote_ids(markdown)


@pytest.mark.parametrize("sample", SAMPLES)
def test_working_report_shows_human_readable_work_segments(sample: str) -> None:
    markdown = _load_report(sample, submitted=False)
    ai_section = _section(markdown, "AI 协作过程")
    assert ai_section.count("### 工作段") >= 2
    assert "自动整理" in ai_section
    assert "不建立轨迹" in ai_section
    assert "完整命令参数" in ai_section
    assert not re.search(r"(?:read|edit|bash)[^\n]{0,24}(?:共计|次数|计数|完成率)", ai_section)
    assert "```" not in ai_section
    assert not EVENT_ID_RE.search(markdown.split("\n---", 1)[0])


@pytest.mark.parametrize("sample", SAMPLES)
def test_submitted_report_excludes_unconfirmed_and_denied_labels(sample: str) -> None:
    markdown = _load_report(sample, submitted=True)
    for forbidden in ("未确认版", "未经本人复核", "待本人确认", "系统线索（学生已否认）"):
        assert forbidden not in markdown
    assert "## AI 协作过程" in markdown
    assert "工作段" in markdown


@pytest.mark.parametrize("sample", SAMPLES)
def test_fact_sections_are_identical_between_report_variants(sample: str) -> None:
    working = _load_report(sample, submitted=False)
    submitted = _load_report(sample, submitted=True)
    for heading in ("项目概述", "开发轨迹", "阶段详情"):
        assert _section(working, heading) == _section(submitted, heading)


@pytest.mark.parametrize("sample", SAMPLES)
def test_report_body_keeps_internal_ids_outside_audit_footnotes(sample: str) -> None:
    for submitted in (False, True):
        markdown = _load_report(sample, submitted=submitted)
        body = markdown.split("\n---", 1)[0]
        assert not re.search(r"(?:evt-|cand-|conf-)", body)
