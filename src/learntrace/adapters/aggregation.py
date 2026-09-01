"""Deterministically group authorized trace events into work segments.

Adapters pass structured, already-normalized metadata into this module. The
grouping code never parses ``ObservableEvent.summary``: summaries are prose,
not a trusted source of paths, tools, or command categories.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

import jsonschema

from learntrace.models import (
    SCHEMA_VERSION,
    ContractValidator,
    EventKind,
    ObservableEvent,
    RecordType,
    SourceType,
)
from learntrace.privacy import CommandCategory, classify_command, normalize_project_path

DEFAULT_SEGMENT_GAP = timedelta(minutes=30)
"""Default idle gap allowed inside one work segment."""

_SAFE_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_BOUNDARIES = frozenset({"time_gap", "object_switch"})


class SegmentBoundary(StrEnum):
    """Why a segment starts after the preceding segment."""

    TIME_GAP = "time_gap"
    OBJECT_SWITCH = "object_switch"


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _string_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError(f"{field_name} must be a sequence of strings")
    items = tuple(cast(Iterable[object], value))
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return tuple(cast(str, item) for item in items)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _safe_relative_path(value: str) -> str | None:
    normalized = normalize_project_path(value, None)
    if normalized.startswith("["):
        return None
    return normalized


def _validate_contract_shape(
    validator: ContractValidator,
    record_type: RecordType,
    raw: Mapping[str, object],
) -> None:
    try:
        validator.validate(record_type, raw)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"{record_type} does not match the v0 schema: {exc.message}") from exc


@dataclass(frozen=True, slots=True)
class TraceEventMetadata:
    """Structured safe fields captured by an adapter for one trace event."""

    event_id: str
    tool: str | None = None
    paths: tuple[str, ...] = ()
    command_category: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("metadata event_id must be non-empty")
        if self.tool is not None and _SAFE_TOOL_RE.fullmatch(self.tool) is None:
            raise ValueError("metadata tool must be a safe tool label")
        paths = _ordered_unique(_string_sequence(self.paths, "metadata paths"))
        for path in paths:
            if _safe_relative_path(path) != path:
                raise ValueError("metadata paths must be normalized project-relative paths")
        object.__setattr__(self, "paths", paths)
        if self.command_category is not None:
            allowed = {category.value for category in CommandCategory}
            if self.command_category not in allowed:
                raise ValueError("metadata command_category must use a fixed category")

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "paths": list(self.paths),
        }
        if self.tool is not None:
            data["tool"] = self.tool
        if self.command_category is not None:
            data["command_category"] = self.command_category
        return data

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, object],
        *,
        validator: ContractValidator | None = None,
    ) -> TraceEventMetadata:
        required = {"schema_version", "event_id", "paths"}
        allowed = {*required, "tool", "command_category"}
        keys = set(raw)
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("trace metadata schema_version must be v0")
        if missing := required - keys:
            raise ValueError(f"trace metadata is missing fields: {', '.join(sorted(missing))}")
        if extra := keys - allowed:
            raise ValueError(f"trace metadata has unknown fields: {', '.join(sorted(extra))}")
        event_id = raw.get("event_id")
        tool = raw.get("tool")
        category = raw.get("command_category")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("trace metadata event_id must be a non-empty string")
        if tool is not None and not isinstance(tool, str):
            raise ValueError("trace metadata tool must be a string or null")
        if category is not None and not isinstance(category, str):
            raise ValueError("trace metadata command_category must be a string or null")
        _validate_contract_shape(
            validator if validator is not None else ContractValidator(),
            "trace_event_metadata",
            raw,
        )
        return cls(
            event_id=event_id,
            tool=tool,
            paths=_string_sequence(raw.get("paths"), "metadata paths"),
            command_category=category,
        )


def build_trace_event_metadata(
    event_id: str,
    *,
    tool: str | None,
    paths: Iterable[str] = (),
    command: str | None = None,
    project_root: Path | None = None,
) -> TraceEventMetadata:
    """Build safe structured metadata directly from adapter fields."""

    safe_tool = None
    if tool is not None:
        safe_tool = tool.casefold() if _SAFE_TOOL_RE.fullmatch(tool) else "unknown-tool"
    safe_paths = _ordered_unique(
        normalized
        for raw_path in paths
        if not (normalized := normalize_project_path(raw_path, project_root)).startswith("[")
    )
    category = classify_command(command).value if command is not None else None
    return TraceEventMetadata(
        event_id=event_id,
        tool=safe_tool,
        paths=safe_paths,
        command_category=category,
    )


def event_merge_key(event: ObservableEvent) -> dict[str, Any]:
    """Return the existing adapter merge key for duplicate trace exports."""

    data = event.to_dict()
    data["source_refs"] = [
        ref
        for ref in data["source_refs"]
        if not (ref.get("type") == SourceType.FILE.value and ref.get("note") == "session-export")
    ]
    return data


def events_conflict(left: ObservableEvent, right: ObservableEvent) -> bool:
    """Return whether same-ID events differ beyond export-file provenance."""

    return event_merge_key(left) != event_merge_key(right)


def canonical_trace_event(
    left: ObservableEvent,
    right: ObservableEvent,
) -> ObservableEvent:
    """Select the same representative regardless of duplicate input order."""

    if left.id != right.id:
        raise ValueError("canonical trace events must have the same id")
    if left.kind != EventKind.TRACE_RECORD or right.kind != EventKind.TRACE_RECORD:
        raise ValueError("canonical trace events must both be trace_record events")
    if events_conflict(left, right):
        raise ValueError(f"conflicting trace events for id {left.id}")
    left_key = json.dumps(left.to_dict(), ensure_ascii=False, sort_keys=True)
    right_key = json.dumps(right.to_dict(), ensure_ascii=False, sort_keys=True)
    return left if left_key <= right_key else right


def _trace_source_metadata(event: ObservableEvent) -> tuple[tuple[str, ...], tuple[str, ...]]:
    hosts: list[str] = []
    sessions: list[str] = []
    for source in event.source_refs:
        if source.type != SourceType.TRACE_RECORD:
            continue
        if (
            source.note
            and source.note != "session-export"
            and _SAFE_EXTERNAL_ID_RE.fullmatch(source.note)
        ):
            hosts.append(source.note.casefold())
        parsed = urlparse(source.ref)
        if parsed.scheme != "trace":
            continue
        if parsed.netloc and _SAFE_EXTERNAL_ID_RE.fullmatch(parsed.netloc):
            hosts.append(parsed.netloc.casefold())
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parts and _SAFE_EXTERNAL_ID_RE.fullmatch(parts[0]):
            sessions.append(parts[0])
    return _ordered_unique(hosts), _ordered_unique(sessions)


@dataclass(frozen=True, slots=True)
class _TraceFacts:
    event: ObservableEvent
    timestamp: datetime | None
    paths: tuple[str, ...]
    scopes: tuple[str, ...]
    tool: str | None
    command_category: str | None
    source_hosts: tuple[str, ...]
    session_ids: tuple[str, ...]


def _path_scope(path: str) -> str | None:
    parts = [part for part in path.split("/") if part and part != "."]
    if not parts or ".." in parts:
        return None
    if len(parts) >= 3:
        return "/".join(parts[:2]).casefold()
    return parts[0].casefold()


def _facts_for_event(
    event: ObservableEvent,
    metadata: TraceEventMetadata | None,
) -> _TraceFacts:
    paths = metadata.paths if metadata is not None else ()
    hosts, sessions = _trace_source_metadata(event)
    return _TraceFacts(
        event=event,
        timestamp=_parse_timestamp(event.occurred_at),
        paths=paths,
        scopes=_ordered_unique(scope for path in paths if (scope := _path_scope(path)) is not None),
        tool=metadata.tool if metadata is not None else None,
        command_category=metadata.command_category if metadata is not None else None,
        source_hosts=hosts,
        session_ids=sessions,
    )


def _facts_sort_key(item: tuple[int, _TraceFacts]) -> tuple[bool, str, str, str, int]:
    index, facts = item
    trace_refs = [
        source.ref for source in facts.event.source_refs if source.type == SourceType.TRACE_RECORD
    ]
    return (
        facts.timestamp is None,
        _timestamp_text(facts.timestamp) or "",
        trace_refs[0] if trace_refs else "",
        facts.event.id,
        index,
    )


def _object_switch(current_scopes: set[str], incoming_scopes: tuple[str, ...]) -> bool:
    return bool(current_scopes and incoming_scopes and current_scopes.isdisjoint(incoming_scopes))


def _segment_id(facts: Sequence[_TraceFacts]) -> str:
    content = "\0".join(fact.event.id for fact in facts)
    return f"segment-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"


@dataclass(frozen=True, slots=True)
class TraceWorkSegment:
    """A deterministic index over a contiguous set of trace event IDs."""

    id: str
    event_ids: tuple[str, ...]
    start_time: str | None
    end_time: str | None
    tools: tuple[str, ...]
    paths: tuple[str, ...]
    command_categories: tuple[str, ...]
    source_hosts: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    boundary_before: str | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("segment id must be non-empty")
        event_ids = _string_sequence(self.event_ids, "event_ids")
        if not event_ids or len(set(event_ids)) != len(event_ids):
            raise ValueError("event_ids must be non-empty and unique")
        object.__setattr__(self, "event_ids", event_ids)
        for field_name in (
            "tools",
            "paths",
            "command_categories",
            "source_hosts",
            "session_ids",
        ):
            values = _ordered_unique(
                _string_sequence(cast(Sequence[str], getattr(self, field_name)), field_name)
            )
            object.__setattr__(self, field_name, values)
        for path in self.paths:
            if _safe_relative_path(path) != path:
                raise ValueError("segment paths must be normalized project-relative paths")
        allowed_categories = {category.value for category in CommandCategory}
        if set(self.command_categories) - allowed_categories:
            raise ValueError("segment command_categories must use fixed categories")
        start = _parse_timestamp(self.start_time)
        end = _parse_timestamp(self.end_time)
        if self.start_time is not None and start is None:
            raise ValueError("segment start_time must be a timezone-aware date-time")
        if self.end_time is not None and end is None:
            raise ValueError("segment end_time must be a timezone-aware date-time")
        if start is not None and end is not None and start > end:
            raise ValueError("segment start_time must not be after end_time")
        if self.boundary_before not in (None, *_BOUNDARIES):
            raise ValueError("unsupported segment boundary")

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "event_ids": list(self.event_ids),
            "time_range": {"start": self.start_time, "end": self.end_time},
            "tools": list(self.tools),
            "paths": list(self.paths),
            "command_categories": list(self.command_categories),
            "source_hosts": list(self.source_hosts),
            "session_ids": list(self.session_ids),
        }
        if self.boundary_before is not None:
            data["boundary_before"] = self.boundary_before
        return data

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, object],
        *,
        validator: ContractValidator | None = None,
    ) -> TraceWorkSegment:
        required = {
            "schema_version",
            "id",
            "event_ids",
            "time_range",
            "tools",
            "paths",
            "command_categories",
            "source_hosts",
            "session_ids",
        }
        allowed = {*required, "boundary_before"}
        keys = set(raw)
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("work segment schema_version must be v0")
        if missing := required - keys:
            raise ValueError(f"work segment is missing fields: {', '.join(sorted(missing))}")
        if extra := keys - allowed:
            raise ValueError(f"work segment has unknown fields: {', '.join(sorted(extra))}")
        time_range = raw.get("time_range")
        if not isinstance(time_range, dict):
            raise ValueError("work segment time_range must be an object")
        times = cast(Mapping[str, object], time_range)
        start = times.get("start")
        end = times.get("end")
        if start is not None and not isinstance(start, str):
            raise ValueError("work segment time_range.start must be a string or null")
        if end is not None and not isinstance(end, str):
            raise ValueError("work segment time_range.end must be a string or null")
        segment_id = raw.get("id")
        boundary = raw.get("boundary_before")
        if not isinstance(segment_id, str) or not segment_id:
            raise ValueError("work segment id must be a non-empty string")
        if boundary is not None and not isinstance(boundary, str):
            raise ValueError("work segment boundary_before must be a string or null")
        _validate_contract_shape(
            validator if validator is not None else ContractValidator(),
            "trace_work_segment",
            raw,
        )
        return cls(
            id=segment_id,
            event_ids=_string_sequence(raw.get("event_ids"), "event_ids"),
            start_time=start,
            end_time=end,
            tools=_string_sequence(raw.get("tools"), "tools"),
            paths=_string_sequence(raw.get("paths"), "paths"),
            command_categories=_string_sequence(
                raw.get("command_categories"), "command_categories"
            ),
            source_hosts=_string_sequence(raw.get("source_hosts"), "source_hosts"),
            session_ids=_string_sequence(raw.get("session_ids"), "session_ids"),
            boundary_before=boundary,
        )


def _build_segment(
    facts: Sequence[_TraceFacts],
    *,
    boundary_before: str | None,
) -> TraceWorkSegment:
    timestamps = [fact.timestamp for fact in facts if fact.timestamp is not None]
    return TraceWorkSegment(
        id=_segment_id(facts),
        event_ids=tuple(fact.event.id for fact in facts),
        start_time=_timestamp_text(min(timestamps) if timestamps else None),
        end_time=_timestamp_text(max(timestamps) if timestamps else None),
        tools=_ordered_unique(fact.tool for fact in facts if fact.tool is not None),
        paths=_ordered_unique(path for fact in facts for path in fact.paths),
        command_categories=_ordered_unique(
            fact.command_category for fact in facts if fact.command_category is not None
        ),
        source_hosts=_ordered_unique(host for fact in facts for host in fact.source_hosts),
        session_ids=_ordered_unique(session for fact in facts for session in fact.session_ids),
        boundary_before=boundary_before,
    )


def segment_trace_events(
    events: Iterable[ObservableEvent],
    *,
    metadata: Iterable[TraceEventMetadata] = (),
    gap_threshold: timedelta = DEFAULT_SEGMENT_GAP,
    gap_minutes: float | None = None,
) -> tuple[TraceWorkSegment, ...]:
    """Group trace events by time gap and disjoint project-object scope."""

    if gap_minutes is not None:
        if gap_minutes < 0:
            raise ValueError("gap_minutes must be non-negative")
        gap_threshold = timedelta(minutes=gap_minutes)
    if gap_threshold < timedelta(0):
        raise ValueError("gap_threshold must be non-negative")

    by_id: dict[str, tuple[int, ObservableEvent]] = {}
    for index, event in enumerate(events):
        if event.kind != EventKind.TRACE_RECORD:
            continue
        existing = by_id.get(event.id)
        if existing is not None:
            representative = canonical_trace_event(existing[1], event)
            by_id[event.id] = (min(index, existing[0]), representative)
            continue
        by_id[event.id] = (index, event)

    metadata_by_id: dict[str, TraceEventMetadata] = {}
    for item in metadata:
        if item.event_id not in by_id:
            raise ValueError(f"metadata references missing trace event {item.event_id}")
        existing = metadata_by_id.get(item.event_id)
        if existing is not None and existing != item:
            raise ValueError(f"conflicting metadata for trace event {item.event_id}")
        metadata_by_id[item.event_id] = item

    ordered = sorted(
        (
            (index, _facts_for_event(event, metadata_by_id.get(event.id)))
            for index, event in by_id.values()
        ),
        key=_facts_sort_key,
    )
    if not ordered:
        return ()

    segments: list[TraceWorkSegment] = []
    current: list[_TraceFacts] = []
    current_scopes: set[str] = set()
    previous_time: datetime | None = None
    pending_boundary: str | None = None
    for _, incoming in ordered:
        boundary: str | None = None
        if current:
            if (
                previous_time is not None
                and incoming.timestamp is not None
                and incoming.timestamp - previous_time > gap_threshold
            ):
                boundary = SegmentBoundary.TIME_GAP.value
            elif _object_switch(current_scopes, incoming.scopes):
                boundary = SegmentBoundary.OBJECT_SWITCH.value
        if boundary is not None:
            segments.append(_build_segment(current, boundary_before=pending_boundary))
            current = []
            current_scopes = set()
            pending_boundary = boundary
        current.append(incoming)
        current_scopes.update(incoming.scopes)
        previous_time = incoming.timestamp
    segments.append(_build_segment(current, boundary_before=pending_boundary))
    return tuple(segments)


def validate_work_segments(
    segments: Iterable[TraceWorkSegment],
    events: Iterable[ObservableEvent],
    *,
    metadata: Iterable[TraceEventMetadata] = (),
) -> None:
    """Require an exact match with the deterministic recomputation."""

    supplied = tuple(segments)
    expected = segment_trace_events(events, metadata=metadata)
    if supplied != expected:
        raise ValueError("work segments do not match deterministic trace aggregation")


__all__ = [
    "DEFAULT_SEGMENT_GAP",
    "SegmentBoundary",
    "TraceEventMetadata",
    "TraceWorkSegment",
    "build_trace_event_metadata",
    "canonical_trace_event",
    "event_merge_key",
    "events_conflict",
    "segment_trace_events",
    "validate_work_segments",
]
