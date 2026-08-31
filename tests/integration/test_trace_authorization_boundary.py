"""The archive accepts traces only through an explicit authorized Task 3 result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.adapters import (
    TraceAdapterResult,
    TraceEventMetadata,
    TraceInputStatus,
    adapt_opencode_export,
    segment_trace_events,
    write_trace_result,
)
from learntrace.archive import (
    load_project_artifacts,
    write_learning_record_result,
)
from learntrace.archive import (
    main as archive_main,
)
from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType


def _static_event() -> ObservableEvent:
    return ObservableEvent(
        id="evt-document-safe",
        kind=EventKind.DOCUMENT,
        summary="项目文档记录了实现目标。",
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="README.md:1"),),
    )


def _unbound_trace() -> ObservableEvent:
    return ObservableEvent(
        id="evt-trace-unbound",
        kind=EventKind.TRACE_RECORD,
        summary="OpenCode 工具 read 已完成。",
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref="trace://opencode/session/message/msg/part/part",
                note="opencode",
            ),
        ),
        occurred_at="2026-08-30T01:00:00Z",
    )


def test_generic_scan_ignores_unbound_trace_but_keeps_static_evidence(tmp_path: Path) -> None:
    (tmp_path / "events.json").write_text(
        json.dumps(
            {"events": [_static_event().to_dict(), _unbound_trace().to_dict()]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(tmp_path)

    assert [event.id for event in loaded.events] == ["evt-document-safe"]
    assert [warning.code for warning in loaded.warnings] == ["unbound_trace_record_ignored"]


def test_strict_scan_rejects_unbound_trace(tmp_path: Path) -> None:
    (tmp_path / "trace.json").write_text(
        json.dumps(_unbound_trace().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="explicitly authorized Task 3 result"):
        load_project_artifacts(tmp_path, strict_inputs=True)


def test_canonical_task3_file_is_not_implicitly_ingested_by_scan(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    write_trace_result(result, tmp_path / "task3-result.json")
    (tmp_path / "static.json").write_text(
        json.dumps(_static_event().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(tmp_path)

    assert [event.id for event in loaded.events] == ["evt-document-safe"]


def test_explicit_in_memory_task3_result_enters_archive(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    (tmp_path / "static.json").write_text(
        json.dumps(_static_event().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    output = write_learning_record_result(
        tmp_path,
        output_path=tmp_path / "learning-record.md",
        trace_result=result,
    )

    events = cast(list[dict[str, Any]], output.archive["events"])
    assert any(event["kind"] == "trace_record" for event in events)
    assert "work_segments" not in output.archive
    assert "segment_summaries" not in output.archive


def test_separate_archive_cli_accepts_only_a_named_task3_result(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(fixture, authorized=True, project_root=Path("D:/repo"))
    trace_path = tmp_path / "task3-result.json"
    archive_path = tmp_path / "archive-records.json"
    write_trace_result(result, trace_path)

    assert (
        archive_main(
            [
                str(tmp_path),
                "--trace-result",
                str(trace_path),
                "--output",
                str(tmp_path / "learning-record.md"),
                "--records-output",
                str(archive_path),
            ]
        )
        == 0
    )

    archive = cast(dict[str, Any], json.loads(archive_path.read_text(encoding="utf-8")))
    events = cast(list[dict[str, Any]], archive["events"])
    assert events
    assert {event["kind"] for event in events} == {"trace_record"}


def test_nonparsed_result_cannot_smuggle_trace_events(tmp_path: Path) -> None:
    (tmp_path / "static.json").write_text(
        json.dumps(_static_event().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    smuggled = TraceAdapterResult(
        status=TraceInputStatus.NOT_AUTHORIZED,
        events=(_unbound_trace(),),
    )

    with pytest.raises(ValueError, match="non-parsed Task 3 result"):
        write_learning_record_result(tmp_path, trace_result=smuggled)


def test_explicit_batch_still_requires_adapter_provenance(tmp_path: Path) -> None:
    (tmp_path / "static.json").write_text(
        json.dumps(_static_event().to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    unbound = _unbound_trace()
    metadata = TraceEventMetadata(event_id=unbound.id)
    forged = TraceAdapterResult(
        status=TraceInputStatus.PARSED,
        events=(unbound,),
        work_segments=segment_trace_events((unbound,), metadata=(metadata,)),
        trace_metadata=(metadata,),
    )

    with pytest.raises(ValueError, match="invalid Task 3 trace_result"):
        write_learning_record_result(tmp_path, trace_result=forged)
