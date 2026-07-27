"""Task 3 batch status and safe parse diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from learntrace.models import ObservableEvent


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

    Only ``events`` is part of the stable cross-task data contract.
    """

    status: TraceInputStatus
    events: tuple[ObservableEvent, ...] = ()
    warnings: tuple[TraceParseIssue, ...] = ()


class UnsupportedOpenCodeFormatError(ValueError):
    """The supplied file is not a supported OpenCode JSON export."""
