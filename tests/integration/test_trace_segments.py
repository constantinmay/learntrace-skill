"""End-to-end coverage for the additive Issue #28 segment contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from learntrace.adapters import (
    TraceSegmentSummary,
    TraceWorkSegment,
    adapt_opencode_export,
    write_trace_result,
)
from learntrace.archive import load_project_artifacts, write_learning_record_result
from learntrace.reporting.pipeline import build_archive_bundle, bundle_to_dict
from learntrace.reporting.render import render_markdown


def test_task3_result_carries_segments_and_archive_reads_them(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    result_path = tmp_path / "task3-result.json"

    write_trace_result(result, result_path)
    payload = cast(dict[str, Any], json.loads(result_path.read_text(encoding="utf-8")))

    assert payload["work_segments"]
    assert all("event_ids" in segment for segment in payload["work_segments"])
    loaded = load_project_artifacts(tmp_path)
    assert len(loaded.work_segments) == len(result.work_segments)
    assert [segment.event_ids for segment in loaded.work_segments] == [
        segment.event_ids for segment in result.work_segments
    ]


def test_segment_summary_is_traceable_and_denial_hides_only_the_summary(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    write_trace_result(result, tmp_path / "task3-result.json")
    segment = result.work_segments[0]

    summary_path = tmp_path / "segment-summaries.json"
    summary = TraceSegmentSummary(
        segment_id=segment.id,
        label="已否认的摘要",
        body="这段文字不应进入正文。",
        event_ids=segment.event_ids,
    )
    summary_path.write_text(
        json.dumps(
            {"segment_summaries": [{**summary.to_dict(), "status": "denied"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(tmp_path)
    bundle = build_archive_bundle(
        loaded.events,
        warnings=loaded.warnings,
        work_segments=loaded.work_segments,
        segment_summaries=loaded.segment_summaries,
    )
    markdown = render_markdown(bundle)
    assert "这段文字不应进入正文" not in markdown.split("<details>", maxsplit=1)[0]
    assert "这段文字不应进入正文" not in markdown
    assert summary.id in markdown
    archive = bundle_to_dict(bundle)
    stored_summaries = cast(list[dict[str, Any]], archive["segment_summaries"])
    assert stored_summaries[0]["status"] == "denied"
    assert cast(list[object], archive["events"])


def test_write_learning_record_result_preserves_segment_fields(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    write_trace_result(result, tmp_path / "task3-result.json")
    output = write_learning_record_result(
        tmp_path,
        output_path=tmp_path / "learning-record.md",
        records_output_path=tmp_path / "archive-records.json",
    )

    assert output.archive["work_segments"]
    assert output.records_output_path is not None
    stored = cast(
        dict[str, Any],
        json.loads(output.records_output_path.read_text(encoding="utf-8")),
    )
    assert stored["record_counts"]["work_segments"] == len(result.work_segments)


def test_archive_keeps_work_segments_in_time_order(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    first_event, second_event = result.events[:2]
    early = TraceWorkSegment(
        id="segment-z-early",
        event_ids=(first_event.id,),
        start_time="2026-08-30T01:00:00Z",
        end_time="2026-08-30T01:01:00Z",
        tools=("read",),
        paths=("src/app.py",),
        command_categories=(),
    )
    late = TraceWorkSegment(
        id="segment-a-late",
        event_ids=(second_event.id,),
        start_time="2026-08-30T02:00:00Z",
        end_time="2026-08-30T02:01:00Z",
        tools=("bash",),
        paths=(),
        command_categories=("其他",),
    )

    bundle = build_archive_bundle(
        result.events,
        work_segments=(late, early),
    )

    assert [segment.id for segment in bundle.work_segments] == [early.id, late.id]
