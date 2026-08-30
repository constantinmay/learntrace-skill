"""Task 3 batch status and safe parse diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from learntrace.adapters.aggregation import TraceWorkSegment
from learntrace.models import ObservableEvent, SourceRef, SourceType
from learntrace.privacy import normalize_project_path


class TraceInputStatus(StrEnum):
    """Why an adapter did or did not produce authorized trace events."""

    NOT_PROVIDED = "not_provided"
    NOT_AUTHORIZED = "not_authorized"
    AUTHORIZED_NOT_FOUND = "authorized_not_found"
    PARSED = "parsed"


@dataclass(frozen=True, slots=True)
class TraceParseIssue:
    """A source-safe warning that never copies raw trace content."""

    code: str
    location: str
    message: str


@dataclass(frozen=True, slots=True)
class TraceAdapterResult:
    """Task 3's local batch container.

    ``events`` remains the atomic cross-task contract. ``work_segments`` is an
    additive deterministic index for consumers that want contiguous work
    periods; old callers can omit it and continue to read the result.
    """

    status: TraceInputStatus
    events: tuple[ObservableEvent, ...] = ()
    warnings: tuple[TraceParseIssue, ...] = ()
    work_segments: tuple[TraceWorkSegment, ...] = ()

    @property
    def segments(self) -> tuple[TraceWorkSegment, ...]:
        """Short alias retained for consumers that call them ``segments``."""

        return self.work_segments


def session_export_source_ref(
    export_path: Path,
    project_root: Path | None,
) -> SourceRef:
    """Return a privacy-safe pointer to the authorized session export.

    The adapter still receives the real path so it can read the explicitly
    authorized input, but persisted trace records must not copy a host's
    absolute username/home path.  Keep a project-relative path when it is
    safe; otherwise use the existing deterministic placeholders from the
    path normalizer.  The trace-record reference remains the authoritative
    citation for the original tool part.
    """

    return SourceRef(
        type=SourceType.FILE,
        ref=normalize_project_path(export_path.as_posix(), project_root),
        note="session-export",
    )


def event_merge_key(event: ObservableEvent) -> dict[str, Any]:
    """事件合并比较键：忽略「会话导出文件路径」参考（note="session-export"）。

    同一条工具记录出现在不同导出文件中（重叠会话）时仍视为相同重复
    （去重并产生 duplicate_export_event 警告）；真正的内容冲突（不同
    message/part）仍会被显式拒绝。
    """
    data = event.to_dict()
    data["source_refs"] = [
        ref for ref in data["source_refs"] if ref.get("note") != "session-export"
    ]
    return data


def events_conflict(left: ObservableEvent, right: ObservableEvent) -> bool:
    """判断两条同 ID 事件的真正内容是否冲突（比较规则见 ``event_merge_key``）。"""
    return event_merge_key(left) != event_merge_key(right)


class UnsupportedOpenCodeFormatError(ValueError):
    """The supplied file is not a supported OpenCode JSON export."""


class UnsupportedTraceFormatError(ValueError):
    """The supplied file is not a supported Claude Code or Codex JSONL trace."""
