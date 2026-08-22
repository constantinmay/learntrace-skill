"""AI coding-agent trace adapters."""

from learntrace.adapters.opencode import adapt_opencode_export, adapt_opencode_exports
from learntrace.adapters.serialization import write_trace_result
from learntrace.adapters.types import (
    TraceAdapterResult,
    TraceInputStatus,
    TraceParseIssue,
    UnsupportedOpenCodeFormatError,
)

__all__ = [
    "TraceAdapterResult",
    "TraceInputStatus",
    "TraceParseIssue",
    "UnsupportedOpenCodeFormatError",
    "adapt_opencode_export",
    "adapt_opencode_exports",
    "write_trace_result",
]
