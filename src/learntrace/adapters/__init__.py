"""AI coding-agent trace adapters."""

from learntrace.adapters.aggregation import (
    DEFAULT_SEGMENT_GAP,
    SegmentBoundary,
    TraceEventMetadata,
    TraceWorkSegment,
    build_trace_event_metadata,
    canonical_trace_event,
    segment_trace_events,
    validate_work_segments,
)
from learntrace.adapters.claude_code import adapt_claude_code_export, adapt_claude_code_exports
from learntrace.adapters.codex import adapt_codex_export, adapt_codex_exports
from learntrace.adapters.opencode import adapt_opencode_export, adapt_opencode_exports
from learntrace.adapters.serialization import (
    read_trace_result,
    trace_result_to_dict,
    write_trace_result,
)
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
    "TraceEventMetadata",
    "TraceWorkSegment",
    "build_trace_event_metadata",
    "canonical_trace_event",
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
    "read_trace_result",
    "segment_trace_events",
    "trace_result_to_dict",
    "validate_work_segments",
    "write_trace_result",
]
