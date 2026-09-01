"""Tests for the small, atomic Task 3 result writer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NoReturn

import pytest

import learntrace.adapters.serialization as serialization
from learntrace.adapters import (
    TraceAdapterResult,
    TraceInputStatus,
    build_trace_event_metadata,
    read_trace_result,
    segment_trace_events,
    write_trace_result,
)
from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType


def _result() -> TraceAdapterResult:
    event = ObservableEvent(
        id="evt-trace-test",
        kind=EventKind.TRACE_RECORD,
        summary="OpenCode 工具 read 已完成。 路径：src/app.py。",
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref="trace://opencode/ses/message/msg/part/prt",
                note="opencode",
            ),
            SourceRef(type=SourceType.FILE, ref="[absolute-path]", note="session-export"),
        ),
        occurred_at="2026-07-27T00:00:00Z",
    )
    metadata = build_trace_event_metadata(event.id, tool="read", paths=("src/app.py",))
    return TraceAdapterResult(
        status=TraceInputStatus.PARSED,
        events=(event,),
        work_segments=segment_trace_events((event,), metadata=(metadata,)),
        trace_metadata=(metadata,),
    )


def test_write_trace_result_serializes_the_task3_container(tmp_path: Path) -> None:
    output_path = tmp_path / "trace-result.json"

    result = _result()
    write_trace_result(result, output_path)

    payload: object = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload == {
        "artifact_type": "learntrace_task3_result",
        "adapter_version": "v0",
        "status": "parsed",
        "events": [
            {
                "schema_version": "v0",
                "id": "evt-trace-test",
                "evidence_level": "observable_fact",
                "kind": "trace_record",
                "summary": "OpenCode 工具 read 已完成。 路径：src/app.py。",
                "source_refs": [
                    {
                        "type": "trace_record",
                        "ref": "trace://opencode/ses/message/msg/part/prt",
                        "note": "opencode",
                    },
                    {
                        "type": "file",
                        "ref": "[absolute-path]",
                        "note": "session-export",
                    },
                ],
                "occurred_at": "2026-07-27T00:00:00Z",
            }
        ],
        "warnings": [],
        "trace_metadata": [result.trace_metadata[0].to_dict()],
        "work_segments": [result.work_segments[0].to_dict()],
    }


def test_read_trace_result_round_trips_the_canonical_batch(tmp_path: Path) -> None:
    output_path = tmp_path / "trace-result.json"
    result = _result()
    write_trace_result(result, output_path)

    assert read_trace_result(output_path) == result


def test_read_trace_result_rejects_inconsistent_segment_data(tmp_path: Path) -> None:
    output_path = tmp_path / "trace-result.json"
    write_trace_result(_result(), output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["work_segments"][0]["paths"] = ["src/forged.py"]
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="do not match deterministic trace aggregation"):
        read_trace_result(output_path)


def test_write_trace_result_removes_its_temp_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "trace-result.json"
    output_path.write_text("old", encoding="utf-8")
    files_before = set(tmp_path.iterdir())

    def fail_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target: object,
    ) -> NoReturn:
        del source, target
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(serialization.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replacement failure"):
        write_trace_result(_result(), output_path)

    assert output_path.read_text(encoding="utf-8") == "old"
    assert set(tmp_path.iterdir()) == files_before
