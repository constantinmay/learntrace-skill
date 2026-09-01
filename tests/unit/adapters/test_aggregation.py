"""Extraction-level tests for Issue #28 trace work segments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from learntrace.adapters import (
    TraceEventMetadata,
    TraceWorkSegment,
    build_trace_event_metadata,
    events_conflict,
    segment_trace_events,
    validate_work_segments,
)
from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.privacy import CommandCategory, classify_command


def _event(
    event_id: str,
    occurred_at: str | None,
    *,
    host: str = "opencode",
    export_ref: str = "[absolute-path]",
    summary: str = "授权工具记录。",
) -> ObservableEvent:
    return ObservableEvent(
        id=event_id,
        kind=EventKind.TRACE_RECORD,
        summary=summary,
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref=f"trace://{host}/session-1/message/{event_id}",
                note=host,
            ),
            SourceRef(type=SourceType.FILE, ref=export_ref, note="session-export"),
        ),
        occurred_at=occurred_at,
    )


def _metadata(
    event: ObservableEvent,
    *,
    tool: str = "read",
    paths: tuple[str, ...] = (),
    command: str | None = None,
) -> TraceEventMetadata:
    return build_trace_event_metadata(
        event.id,
        tool=tool,
        paths=paths,
        command=command,
    )


def test_segments_use_strictly_greater_than_thirty_minute_gap() -> None:
    events = (
        _event("evt-a", "2026-08-30T01:00:00Z"),
        _event("evt-b", "2026-08-30T01:30:00Z"),
        _event("evt-c", "2026-08-30T02:00:01Z"),
    )

    segments = segment_trace_events(events, metadata=tuple(_metadata(event) for event in events))

    assert [segment.event_ids for segment in segments] == [("evt-a", "evt-b"), ("evt-c",)]
    assert segments[1].boundary_before == "time_gap"


def test_segments_split_when_structured_project_scope_switches() -> None:
    front = _event("evt-front", "2026-08-30T01:00:00Z")
    back = _event("evt-back", "2026-08-30T01:01:00Z")

    segments = segment_trace_events(
        (front, back),
        metadata=(
            _metadata(front, tool="edit", paths=("src/frontend/App.jsx",)),
            _metadata(back, tool="edit", paths=("src/backend/api.py",)),
        ),
    )

    assert [segment.event_ids for segment in segments] == [("evt-front",), ("evt-back",)]
    assert segments[1].boundary_before == "object_switch"


def test_summary_text_is_never_parsed_as_structured_metadata() -> None:
    event = _event(
        "evt-prose",
        "2026-08-30T01:00:00Z",
        summary="OpenCode 工具 read 已完成。路径：/home/alice/private.py。",
    )

    segment = segment_trace_events((event,))[0]

    assert segment.paths == ()
    assert segment.tools == ()
    assert "/home/alice" not in str(segment.to_dict())


def test_missing_timestamps_do_not_create_a_guessed_boundary() -> None:
    events = (_event("evt-a", None), _event("evt-b", None))

    segments = segment_trace_events(events, metadata=tuple(_metadata(event) for event in events))

    assert len(segments) == 1
    assert segments[0].start_time is None
    assert segments[0].end_time is None


def test_unknown_timestamp_breaks_gap_comparison_without_forcing_a_split() -> None:
    before = _event("evt-before", "2026-08-30T01:00:00Z")
    unknown = _event("evt-unknown", None)
    after = _event("evt-after", "2026-08-30T03:00:00Z")
    events = (before, unknown, after)

    segments = segment_trace_events(events, metadata=tuple(_metadata(event) for event in events))

    assert [segment.event_ids for segment in segments] == [
        ("evt-before",),
        ("evt-after", "evt-unknown"),
    ]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pip install -r requirements.txt", CommandCategory.INSTALL),
        ("uv run pytest tests/", CommandCategory.TEST),
        ("cd frontend && npm run build -- --mode=prod", CommandCategory.BUILD_DEPLOY),
        ("git status --short", CommandCategory.INSPECT),
        ("echo hello", CommandCategory.OTHER),
    ],
)
def test_command_categories_are_fixed_and_deterministic(
    command: str,
    expected: CommandCategory,
) -> None:
    assert classify_command(command) == expected


def test_segment_uses_adapter_metadata_without_copying_command_arguments() -> None:
    event = _event("evt-build", "2026-08-30T01:00:00Z")
    metadata = _metadata(
        event,
        tool="bash",
        command="cd frontend && npm run build -- --mode=prod",
    )

    segment = segment_trace_events((event,), metadata=(metadata,))[0]

    assert segment.command_categories == (CommandCategory.BUILD_DEPLOY.value,)
    assert "frontend" not in str(segment.to_dict())
    assert "mode=prod" not in str(segment.to_dict())


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/home/alice/private.py",
        "C:\\Users\\alice\\private.py",
        "\\\\server\\share\\private.py",
        "~/private.py",
        "$HOME/private.py",
        "../private.py",
        "file:///home/alice/private.py",
        " /home/alice/private.py",
        " C:/Users/Alice/private.py",
        "%2Fhome%2Falice/private.py",
    ],
)
def test_metadata_builder_drops_paths_outside_the_project(unsafe_path: str) -> None:
    event = _event("evt-private", "2026-08-30T01:00:00Z")

    metadata = _metadata(event, paths=(unsafe_path,))
    segment = segment_trace_events((event,), metadata=(metadata,))[0]

    assert metadata.paths == ()
    assert segment.paths == ()
    assert "alice" not in str(segment.to_dict()).casefold()


def test_metadata_builder_preserves_normalized_relative_paths() -> None:
    event = _event("evt-relative", "2026-08-30T01:00:00Z")

    metadata = _metadata(event, paths=("src/my component/app.py",))

    assert segment_trace_events((event,), metadata=(metadata,))[0].paths == (
        "src/my component/app.py",
    )


def test_non_trace_source_notes_are_not_treated_as_hosts() -> None:
    event = ObservableEvent(
        id="evt-file-note",
        kind=EventKind.TRACE_RECORD,
        summary="授权工具记录。",
        source_refs=(
            SourceRef(type=SourceType.TRACE_RECORD, ref="trace://opencode/session-1/item-1"),
            SourceRef(type=SourceType.FILE, ref="src/app.py", note="private-user-label"),
        ),
        occurred_at="2026-08-30T01:00:00Z",
    )

    segment = segment_trace_events((event,), metadata=(_metadata(event),))[0]

    assert segment.source_hosts == ("opencode",)


def test_segment_rejects_unsafe_external_paths_and_categories() -> None:
    with pytest.raises(ValueError, match="project-relative"):
        TraceWorkSegment(
            id="segment-bad-path",
            event_ids=("evt-safe",),
            start_time=None,
            end_time=None,
            tools=(),
            paths=("/home/alice/private.py",),
            command_categories=(),
        )
    with pytest.raises(ValueError, match="fixed categories"):
        TraceWorkSegment(
            id="segment-bad-command",
            event_ids=("evt-safe",),
            start_time=None,
            end_time=None,
            tools=(),
            paths=(),
            command_categories=("raw command --secret",),
        )


def test_segment_ids_and_fields_are_stable_when_input_order_changes() -> None:
    events = (
        _event("evt-a", "2026-08-30T01:00:00Z"),
        _event("evt-b", "2026-08-30T01:01:00Z"),
    )
    metadata = tuple(_metadata(event, paths=("src/app.py",)) for event in events)

    forward = segment_trace_events(events, metadata=metadata)
    reverse = segment_trace_events(tuple(reversed(events)), metadata=tuple(reversed(metadata)))

    assert [segment.to_dict() for segment in forward] == [segment.to_dict() for segment in reverse]


def test_duplicate_exports_use_the_shared_event_conflict_rule() -> None:
    first = _event("evt-dup", "2026-08-30T01:00:00Z", export_ref="export-a.json")
    second = _event("evt-dup", "2026-08-30T01:00:00Z", export_ref="export-b.json")
    metadata = _metadata(first, paths=("src/app.py",))

    assert events_conflict(first, second) is False
    assert segment_trace_events((first, second), metadata=(metadata,))[0].event_ids == ("evt-dup",)


def test_duplicate_event_with_real_content_change_is_rejected() -> None:
    first = _event("evt-dup", "2026-08-30T01:00:00Z")
    changed = _event("evt-dup", "2026-08-30T01:00:00Z", summary="不同的事实摘要。")

    with pytest.raises(ValueError, match="conflicting trace events"):
        segment_trace_events((first, changed))


def test_session_export_note_never_hides_a_changed_trace_reference() -> None:
    first = ObservableEvent(
        id="evt-dup",
        kind=EventKind.TRACE_RECORD,
        summary="授权工具记录。",
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref="trace://opencode/session-a/item",
                note="session-export",
            ),
        ),
    )
    changed = replace(
        first,
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref="trace://opencode/session-b/item",
                note="session-export",
            ),
        ),
    )

    assert events_conflict(first, changed) is True


def _reverse_event_ids(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, event_ids=tuple(reversed(segment.event_ids)))


def _forge_start_time(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, start_time="2026-08-29T01:00:00Z")


def _forge_tools(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, tools=("write",))


def _forge_paths(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, paths=("src/forged.py",))


def _forge_command_categories(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, command_categories=(CommandCategory.OTHER.value,))


def _forge_source_hosts(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, source_hosts=("codex",))


def _forge_segment_id(segment: TraceWorkSegment) -> TraceWorkSegment:
    return replace(segment, id="segment-forged")


@pytest.mark.parametrize(
    "mutation",
    [
        _reverse_event_ids,
        _forge_start_time,
        _forge_tools,
        _forge_paths,
        _forge_command_categories,
        _forge_source_hosts,
        _forge_segment_id,
    ],
)
def test_validation_rejects_tampered_segments(
    mutation: Callable[[TraceWorkSegment], TraceWorkSegment],
) -> None:
    first = _event("evt-a", "2026-08-30T01:00:00Z")
    second = _event("evt-b", "2026-08-30T01:01:00Z")
    events = (first, second)
    metadata = (
        _metadata(first, tool="read", paths=("src/app.py",)),
        _metadata(second, tool="read", paths=("src/app.py",)),
    )
    segment = segment_trace_events(events, metadata=metadata)[0]

    changed = mutation(segment)
    with pytest.raises(ValueError, match="do not match"):
        validate_work_segments((changed,), events, metadata=metadata)


def test_validation_rejects_omitted_or_repeated_events() -> None:
    first = _event("evt-a", "2026-08-30T01:00:00Z")
    second = _event("evt-b", "2026-08-30T02:00:00Z")
    events = (first, second)
    metadata = (_metadata(first), _metadata(second))
    expected = segment_trace_events(events, metadata=metadata)

    with pytest.raises(ValueError, match="do not match"):
        validate_work_segments(expected[:1], events, metadata=metadata)
    repeated = (expected[0], replace(expected[1], event_ids=expected[0].event_ids))
    with pytest.raises(ValueError, match="do not match"):
        validate_work_segments(repeated, events, metadata=metadata)


def test_from_dict_accepts_only_the_canonical_shape() -> None:
    event = _event("evt-roundtrip", "2026-08-30T01:00:00Z")
    metadata = _metadata(event)
    segment = segment_trace_events((event,), metadata=(metadata,))[0]

    assert TraceEventMetadata.from_dict(metadata.to_dict()) == metadata
    assert TraceWorkSegment.from_dict(segment.to_dict()) == segment
    with pytest.raises(ValueError, match="schema_version"):
        TraceWorkSegment.from_dict({"id": "segment-old", "event_ids": [event.id]})
    with pytest.raises(ValueError, match="unknown fields"):
        TraceWorkSegment.from_dict({**segment.to_dict(), "forged": True})
    with pytest.raises(ValueError, match="schema_version"):
        TraceEventMetadata.from_dict({"event_id": event.id, "paths": []})
    with pytest.raises(ValueError, match="unknown fields"):
        TraceEventMetadata.from_dict({**metadata.to_dict(), "forged": True})


def test_metadata_from_dict_rejects_duplicate_paths_from_schema() -> None:
    event = _event("evt-metadata-schema", "2026-08-30T01:00:00Z")
    payload = _metadata(event, paths=("src/app.py",)).to_dict()
    payload["paths"] = ["src/app.py", "src/app.py"]

    with pytest.raises(ValueError, match="v0 schema"):
        TraceEventMetadata.from_dict(payload)


def test_segment_from_dict_rejects_noncanonical_nested_shapes() -> None:
    event = _event("evt-segment-schema", "2026-08-30T01:00:00Z")
    metadata = _metadata(event, paths=("src/app.py",))
    base = segment_trace_events((event,), metadata=(metadata,))[0].to_dict()

    missing_time_field = {**base, "time_range": {"start": None}}
    extra_time_field = {
        **base,
        "time_range": {"start": None, "end": None, "forged": True},
    }
    unsafe_tool = {**base, "tools": ["bad tool"]}
    unsafe_host = {**base, "source_hosts": ["bad host"]}
    duplicate_paths = {**base, "paths": ["src/app.py", "src/app.py"]}

    for payload in (
        missing_time_field,
        extra_time_field,
        unsafe_tool,
        unsafe_host,
        duplicate_paths,
    ):
        with pytest.raises(ValueError, match="v0 schema"):
            TraceWorkSegment.from_dict(payload)
