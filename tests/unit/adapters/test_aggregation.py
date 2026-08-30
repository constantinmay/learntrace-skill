"""Extraction-level tests for Issue #28 trace work segments."""

from __future__ import annotations

import pytest

from learntrace.adapters import (
    SegmentSummary,
    TraceSegmentSummary,
    TraceWorkSegment,
    aggregate_trace_events,
    segment_trace_events,
)
from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.privacy import CommandCategory, classify_command


def _event(
    event_id: str,
    occurred_at: str | None,
    summary: str,
    *,
    host: str = "opencode",
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
        ),
        occurred_at=occurred_at,
    )


def test_segments_use_strictly_greater_than_thirty_minute_gap() -> None:
    events = (
        _event("evt-a", "2026-08-30T01:00:00Z", "OpenCode 工具 read 已完成。路径：src/app.py。"),
        _event("evt-b", "2026-08-30T01:30:00Z", "OpenCode 工具 edit 已完成。路径：src/main.py。"),
        _event("evt-c", "2026-08-30T02:00:01Z", "OpenCode 工具 bash 已完成。命令类型：pytest。"),
    )

    segments = segment_trace_events(events)

    assert [segment.event_ids for segment in segments] == [
        ("evt-a", "evt-b"),
        ("evt-c",),
    ]
    assert segments[1].boundary_before == "time_gap"


def test_segments_split_when_project_object_scope_switches() -> None:
    events = (
        _event(
            "evt-front",
            "2026-08-30T01:00:00Z",
            "OpenCode 工具 edit 已完成。路径：src/frontend/App.jsx。",
        ),
        _event(
            "evt-back",
            "2026-08-30T01:01:00Z",
            "OpenCode 工具 edit 已完成。路径：src/backend/api.py。",
        ),
    )

    segments = aggregate_trace_events(events)

    assert [segment.event_ids for segment in segments] == [("evt-front",), ("evt-back",)]
    assert segments[1].boundary_before == "object_switch"


def test_missing_timestamps_do_not_create_a_guessed_boundary() -> None:
    events = (
        _event("evt-a", None, "OpenCode 工具 read 已完成。路径：src/app.py。"),
        _event("evt-b", None, "OpenCode 工具 edit 已完成。路径：src/main.py。"),
    )

    segments = segment_trace_events(events)

    assert len(segments) == 1
    assert segments[0].time_range == {"start": None, "end": None}


def test_unknown_timestamp_breaks_gap_comparison_without_forcing_a_split() -> None:
    events = (
        _event(
            "evt-before", "2026-08-30T01:00:00Z", "OpenCode 工具 read 已完成。路径：src/app.py。"
        ),
        _event("evt-unknown", None, "OpenCode 工具 edit 已完成。路径：src/main.py。"),
        _event(
            "evt-after", "2026-08-30T03:00:00Z", "OpenCode 工具 edit 已完成。路径：src/main.py。"
        ),
    )

    segments = segment_trace_events(events)

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
        ("装依赖", CommandCategory.INSTALL),
        ("构建部署", CommandCategory.BUILD_DEPLOY),
    ],
)
def test_command_categories_are_safe_and_deterministic(
    command: str,
    expected: CommandCategory,
) -> None:
    assert classify_command(command) == expected


def test_segment_collects_categories_without_copying_command_arguments() -> None:
    event = _event(
        "evt-build",
        "2026-08-30T01:00:00Z",
        "OpenCode 工具 bash 已完成。命令类型：cd frontend && npm run build -- --mode=prod。",
    )

    segment = segment_trace_events((event,))[0]

    assert segment.command_categories == (CommandCategory.BUILD_DEPLOY.value,)
    assert "mode=prod" not in str(segment.to_dict())


def test_segment_preserves_spaces_in_a_project_relative_path() -> None:
    event = _event(
        "evt-spaced-path",
        "2026-08-30T01:00:00Z",
        "OpenCode 工具 read 已完成。路径：src/my component/app.py。",
    )

    segment = segment_trace_events((event,))[0]

    assert segment.paths == ("src/my component/app.py",)


def test_non_trace_source_notes_are_not_treated_as_hosts() -> None:
    event = ObservableEvent(
        id="evt-file-note",
        kind=EventKind.TRACE_RECORD,
        summary="OpenCode 工具 read 已完成。路径：src/app.py。",
        source_refs=(
            SourceRef(type=SourceType.TRACE_RECORD, ref="trace://opencode/session-1/item-1"),
            SourceRef(type=SourceType.FILE, ref="src/app.py", note="private-user-label"),
        ),
        occurred_at="2026-08-30T01:00:00Z",
    )

    segment = segment_trace_events((event,))[0]

    assert segment.source_hosts == ("opencode",)


def test_segment_rejects_unknown_command_categories() -> None:
    with pytest.raises(ValueError, match="unsupported command category"):
        TraceWorkSegment(
            id="segment-invalid-category",
            event_ids=("evt-safe",),
            start_time=None,
            end_time=None,
            tools=(),
            paths=(),
            command_categories=("raw command with arguments",),
        )


def test_segment_parser_accepts_early_presentation_aliases() -> None:
    segment = TraceWorkSegment.from_dict(
        {
            "segment_id": "segment-early-shape",
            "citations": ["evt-safe"],
            "coverage": "2026-08-30",
            "activity_shape": ["read"],
            "file_focus": ["src/app.py"],
            "command_categories": ["inspect_files"],
        }
    )

    assert segment.id == "segment-early-shape"
    assert segment.event_ids == ("evt-safe",)
    assert segment.tools == ("read",)
    assert segment.paths == ("src/app.py",)
    assert segment.command_categories == (CommandCategory.INSPECT.value,)


def test_segment_ids_are_stable_when_input_order_changes() -> None:
    events = (
        _event("evt-a", "2026-08-30T01:00:00Z", "OpenCode 工具 read 已完成。路径：src/app.py。"),
        _event("evt-b", "2026-08-30T01:01:00Z", "OpenCode 工具 edit 已完成。路径：src/main.py。"),
    )

    forward = segment_trace_events(events)
    reverse = segment_trace_events(tuple(reversed(events)))

    assert [segment.to_dict() for segment in forward] == [segment.to_dict() for segment in reverse]


def test_host_summary_matches_derived_episode_shape_and_supports_denial() -> None:
    summary = TraceSegmentSummary(
        segment_id="segment-123",
        label="前端联调",
        body="整理了界面调整和测试过程。",
        event_ids=("evt-a",),
        authorization="full",
    )

    assert SegmentSummary is TraceSegmentSummary
    assert summary.to_episode_dict() == {
        "label": "前端联调",
        "body": "整理了界面调整和测试过程。",
        "citations": ["evt-a"],
        "derived": True,
        "derivation": "host_agent_episode_digest",
        "deniable": True,
    }
    denied = TraceSegmentSummary.from_dict({**summary.to_dict(), "status": "denied"})
    assert denied.denied


def test_minimal_agent_projection_keeps_only_safe_fields_and_ids() -> None:
    event = _event(
        "evt-safe",
        "2026-08-30T01:00:00Z",
        "OpenCode 工具 bash 已完成。路径：src/app.py。命令类型：uv run pytest tests/。",
    )
    segment = segment_trace_events((event,))[0]

    minimal = segment.to_agent_input(authorization="minimal")
    full = segment.to_agent_input(authorization="full")

    assert minimal["segment_id"] == segment.id
    assert minimal["event_ids"] == ["evt-safe"]
    assert "session_ids" not in minimal
    assert full["session_ids"] == ["session-1"]
