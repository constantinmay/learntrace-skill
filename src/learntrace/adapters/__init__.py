"""AI coding-agent trace adapters."""

from learntrace.adapters.aggregation import (
    DEFAULT_SEGMENT_GAP,
    SegmentBoundary,
    SegmentSummary,
    TraceSegment,
    TraceSegmentSummary,
    TraceWorkSegment,
    WorkSegment,
    WorkSegmentSummary,
    aggregate_trace_events,
    build_work_segments,
    segment_trace_events,
    validate_segment_summaries,
    validate_work_segments,
)
from learntrace.adapters.claude_code import adapt_claude_code_export, adapt_claude_code_exports
from learntrace.adapters.codex import adapt_codex_export, adapt_codex_exports
from learntrace.adapters.opencode import adapt_opencode_export, adapt_opencode_exports
from learntrace.adapters.serialization import write_trace_result
from learntrace.adapters.types import (
    TraceAdapterResult,
    TraceInputStatus,
    TraceParseIssue,
    UnsupportedOpenCodeFormatError,
    UnsupportedTraceFormatError,
    events_conflict,
)

__all__ = [
    "DEFAULT_SEGMENT_GAP",
    "SegmentBoundary",
    "SegmentSummary",
    "TraceSegment",
    "TraceSegmentSummary",
    "TraceWorkSegment",
    "WorkSegment",
    "WorkSegmentSummary",
    "aggregate_trace_events",
    "TraceAdapterResult",
    "TraceInputStatus",
    "TraceParseIssue",
    "UnsupportedOpenCodeFormatError",
    "UnsupportedTraceFormatError",
    "adapt_claude_code_export",
    "adapt_claude_code_exports",
    "adapt_codex_export",
    "adapt_codex_exports",
    "adapt_opencode_export",
    "adapt_opencode_exports",
    "events_conflict",
    "build_work_segments",
    "segment_trace_events",
    "validate_segment_summaries",
    "validate_work_segments",
    "write_trace_result",
]
