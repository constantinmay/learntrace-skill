"""Formal v0 contracts for Task 3 work segments and result batches."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.adapters import (
    TraceAdapterResult,
    TraceInputStatus,
    build_trace_event_metadata,
    segment_trace_events,
)
from learntrace.adapters.serialization import write_trace_result
from learntrace.models import ContractValidator, EventKind, ObservableEvent, SourceRef, SourceType


def _parsed_result() -> TraceAdapterResult:
    event = ObservableEvent(
        id="evt-trace-contract",
        kind=EventKind.TRACE_RECORD,
        summary="OpenCode 工具 read 已完成。路径：src/app.py。",
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref="trace://opencode/session-1/message/msg/part/part-1",
                note="opencode",
            ),
            SourceRef(type=SourceType.FILE, ref="[absolute-path]", note="session-export"),
        ),
        occurred_at="2026-08-30T01:00:00Z",
    )
    metadata = build_trace_event_metadata(event.id, tool="read", paths=("src/app.py",))
    return TraceAdapterResult(
        status=TraceInputStatus.PARSED,
        events=(event,),
        work_segments=segment_trace_events((event,), metadata=(metadata,)),
        trace_metadata=(metadata,),
    )


def _written_payload(tmp_path: Path) -> dict[str, Any]:
    output = tmp_path / "task3-result.json"
    write_trace_result(_parsed_result(), output)
    return cast(dict[str, Any], json.loads(output.read_text(encoding="utf-8")))


def test_work_segment_and_task3_result_validate_against_v0_schema(tmp_path: Path) -> None:
    validator = ContractValidator()
    result = _parsed_result()

    validator.validate("trace_event_metadata", result.trace_metadata[0].to_dict())
    validator.validate("trace_work_segment", result.work_segments[0].to_dict())
    validator.validate("trace_result", _written_payload(tmp_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("paths", ["/home/alice/private.py"]),
        ("paths", ["../private.py"]),
        ("paths", [r"src\..\private.py"]),
        ("paths", ["[absolute-path]"]),
        ("paths", ["file:/home/alice/private.py"]),
        ("paths", [" /home/alice/private.py"]),
        ("paths", ["%2Fhome%2Falice/private.py"]),
        ("command_categories", ["raw command --secret"]),
        ("event_ids", []),
        ("boundary_before", "guessed"),
    ],
)
def test_work_segment_schema_rejects_invalid_public_fields(field: str, value: object) -> None:
    validator = ContractValidator()
    raw = deepcopy(_parsed_result().work_segments[0].to_dict())
    raw[field] = value

    assert validator.iter_errors("trace_work_segment", raw)


def test_nonparsed_task3_result_cannot_smuggle_events_or_segments(tmp_path: Path) -> None:
    validator = ContractValidator()
    payload = _written_payload(tmp_path)
    payload["status"] = "not_authorized"

    assert validator.iter_errors("trace_result", payload)


def test_trace_result_requires_authorized_adapter_provenance(tmp_path: Path) -> None:
    validator = ContractValidator()
    payload = _written_payload(tmp_path)
    events = cast(list[dict[str, Any]], payload["events"])
    events[0]["source_refs"] = [
        {
            "type": "trace_record",
            "ref": "trace://opencode/session-1/message/msg/part/part-1",
        }
    ]

    assert validator.iter_errors("trace_result", payload)


@pytest.mark.parametrize(
    "source_refs",
    [
        [
            {
                "type": "trace_record",
                "ref": "trace://opencode/session-1/message/msg/part/part-1",
                "note": "opencode",
            },
            {
                "type": "file",
                "ref": "/home/alice/session.json",
                "note": "session-export",
            },
        ],
        [
            {
                "type": "trace_record",
                "ref": "trace://codex/session-1/item-1",
                "note": "opencode",
            },
            {
                "type": "file",
                "ref": "fixtures/session.json",
                "note": "session-export",
            },
        ],
    ],
)
def test_trace_result_rejects_private_or_mismatched_provenance(
    tmp_path: Path,
    source_refs: list[dict[str, str]],
) -> None:
    validator = ContractValidator()
    payload = _written_payload(tmp_path)
    events = cast(list[dict[str, Any]], payload["events"])
    events[0]["source_refs"] = source_refs

    assert validator.iter_errors("trace_result", payload)
