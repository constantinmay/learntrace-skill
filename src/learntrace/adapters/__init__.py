"""AI coding-agent trace adapters."""

from learntrace.adapters.opencode import adapt_opencode_export
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
]
