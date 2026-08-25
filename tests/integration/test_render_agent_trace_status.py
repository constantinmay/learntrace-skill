"""Render coverage for the agent-trace unknown status classification."""

from __future__ import annotations

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.reporting.pipeline import build_archive_bundle
from learntrace.reporting.render import render_markdown


def _trace(event_id: str, summary: str) -> ObservableEvent:
    return ObservableEvent(
        id=event_id,
        kind=EventKind.TRACE_RECORD,
        summary=summary,
        source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref=f"trace://session/{event_id}"),),
    )


def test_unknown_status_trace_is_not_counted_as_completed() -> None:
    markdown = render_markdown(
        build_archive_bundle(
            (_trace("evt-unknown-1", "Codex 工具 shell 结束（状态未知）。 命令类型：bash。"),)
        )
    )

    assert "shell：共 1 次，完成 0 次，错误 0 次，未完成 0 次，状态未知 1 次" in markdown


def test_mixed_statuses_are_counted_separately_per_tool() -> None:
    events = (
        _trace("evt-done-1", "Codex 工具 shell 已完成。 命令类型：git。"),
        _trace("evt-error-1", "Codex 工具 shell 以错误结束。 命令类型：uv run pytest。"),
        _trace("evt-unknown-2", "Codex 工具 shell 结束（状态未知）。 命令类型：cat。"),
    )

    markdown = render_markdown(build_archive_bundle(events))

    assert "shell：共 3 次，完成 1 次，错误 1 次，未完成 0 次，状态未知 1 次" in markdown
