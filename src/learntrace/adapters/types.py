"""Task 3 batch status and safe parse diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from learntrace.adapters.aggregation import (
    TraceEventMetadata,
    TraceWorkSegment,
    canonical_trace_event,
    event_merge_key,
    events_conflict,
)
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
    trace_metadata: tuple[TraceEventMetadata, ...] = ()


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


class UnsupportedOpenCodeFormatError(ValueError):
    """The supplied file is not a supported OpenCode JSON export."""


class UnsupportedTraceFormatError(ValueError):
    """The supplied file is not a supported Claude Code or Codex JSONL trace."""


__all__ = [
    "TraceAdapterResult",
    "TraceInputStatus",
    "TraceParseIssue",
    "UnsupportedOpenCodeFormatError",
    "UnsupportedTraceFormatError",
    "canonical_trace_event",
    "event_merge_key",
    "events_conflict",
    "session_export_source_ref",
]
