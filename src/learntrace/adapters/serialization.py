"""Small, atomic JSON writer for Task 3 adapter results."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import jsonschema

from learntrace.adapters.aggregation import (
    TraceEventMetadata,
    TraceWorkSegment,
    segment_trace_events,
    validate_work_segments,
)
from learntrace.adapters.types import TraceAdapterResult, TraceInputStatus, TraceParseIssue
from learntrace.models import (
    ContractValidator,
    EventKind,
    ObservableEvent,
    RecordType,
    SourceRef,
    SourceType,
)

_ADAPTER_VERSION = "v0"
_MAX_RESULT_BYTES = 64 * 1024 * 1024


def _validate_contract(
    validator: ContractValidator,
    record_type: RecordType,
    payload: object,
) -> None:
    try:
        validator.validate(record_type, payload)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"invalid Task 3 {record_type}: {exc.message}") from exc


def trace_result_to_dict(
    result: TraceAdapterResult,
    *,
    validator: ContractValidator | None = None,
) -> dict[str, object]:
    """Return the canonical, fully validated Task 3 batch payload."""

    contract_validator = validator if validator is not None else ContractValidator()
    events: list[dict[str, object]] = []
    for event in result.events:
        event_data = event.to_dict()
        _validate_contract(contract_validator, "observable_event", event_data)
        events.append(event_data)

    warnings: list[dict[str, str]] = [
        {
            "code": warning.code,
            "location": warning.location,
            "message": warning.message,
        }
        for warning in result.warnings
    ]
    event_ids = tuple(event.id for event in result.events)
    metadata_ids = tuple(item.event_id for item in result.trace_metadata)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Task 3 result must not contain duplicate event ids")
    if len(metadata_ids) != len(set(metadata_ids)) or set(metadata_ids) != set(event_ids):
        raise ValueError("Task 3 trace metadata must contain exactly one item per event")
    metadata_by_id = {item.event_id: item for item in result.trace_metadata}
    ordered_metadata = tuple(metadata_by_id[event_id] for event_id in event_ids)
    metadata_payloads = [item.to_dict() for item in ordered_metadata]
    for metadata in metadata_payloads:
        _validate_contract(contract_validator, "trace_event_metadata", metadata)
    computed_segments = segment_trace_events(result.events, metadata=ordered_metadata)
    if result.work_segments:
        validate_work_segments(
            result.work_segments,
            result.events,
            metadata=result.trace_metadata,
        )
    for segment in computed_segments:
        _validate_contract(contract_validator, "trace_work_segment", segment.to_dict())
    payload: dict[str, object] = {
        "artifact_type": "learntrace_task3_result",
        "adapter_version": _ADAPTER_VERSION,
        "status": str(result.status),
        "events": events,
        "warnings": warnings,
        "trace_metadata": metadata_payloads,
        "work_segments": [segment.to_dict() for segment in computed_segments],
    }
    _validate_contract(contract_validator, "trace_result", payload)
    return payload


def _read_json_object(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    label = path.name or "task3-result.json"
    if not resolved.is_file():
        raise FileNotFoundError(f"Task 3 result does not exist: {label}")
    try:
        if resolved.stat().st_size > _MAX_RESULT_BYTES:
            raise ValueError(f"Task 3 result exceeds {_MAX_RESULT_BYTES} bytes: {label}")
        raw: object = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read Task 3 result {label} ({type(exc).__name__})") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Task 3 result must be a JSON object: {label}")
    return cast(dict[str, Any], raw)


def _parse_event(raw: Mapping[str, Any]) -> ObservableEvent:
    source_refs = tuple(
        SourceRef(
            type=SourceType(str(source["type"])),
            ref=str(source["ref"]),
            note=(str(source["note"]) if "note" in source else None),
        )
        for source in cast(list[dict[str, Any]], raw["source_refs"])
    )
    occurred_at = raw.get("occurred_at")
    return ObservableEvent(
        id=str(raw["id"]),
        kind=EventKind(str(raw["kind"])),
        summary=str(raw["summary"]),
        source_refs=source_refs,
        occurred_at=str(occurred_at) if occurred_at is not None else None,
    )


def read_trace_result(
    path: Path,
    *,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Read a named Task 3 result and reject invalid or internally inconsistent data."""

    contract_validator = validator if validator is not None else ContractValidator()
    raw = _read_json_object(path)
    _validate_contract(contract_validator, "trace_result", raw)

    events = tuple(_parse_event(item) for item in cast(list[dict[str, Any]], raw["events"]))
    warnings = tuple(
        TraceParseIssue(
            code=str(item["code"]),
            location=str(item["location"]),
            message=str(item["message"]),
        )
        for item in cast(list[dict[str, Any]], raw["warnings"])
    )
    metadata = tuple(
        TraceEventMetadata.from_dict(cast(Mapping[str, object], item), validator=contract_validator)
        for item in cast(list[dict[str, Any]], raw["trace_metadata"])
    )
    segments = tuple(
        TraceWorkSegment.from_dict(cast(Mapping[str, object], item), validator=contract_validator)
        for item in cast(list[dict[str, Any]], raw["work_segments"])
    )
    result = TraceAdapterResult(
        status=TraceInputStatus(str(raw["status"])),
        events=events,
        warnings=warnings,
        work_segments=segments,
        trace_metadata=metadata,
    )
    canonical = trace_result_to_dict(result, validator=contract_validator)
    if canonical != raw:
        raise ValueError("Task 3 result does not match deterministic recomputation")
    return result


def write_trace_result(result: TraceAdapterResult, output_path: Path) -> None:
    """Validate and atomically write one Task 3 result as UTF-8 JSON."""

    payload = json.dumps(
        trace_result_to_dict(result),
        ensure_ascii=False,
        indent=2,
    )
    descriptor, temp_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
        os.replace(temp_path, output_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


__all__ = ["read_trace_result", "trace_result_to_dict", "write_trace_result"]
