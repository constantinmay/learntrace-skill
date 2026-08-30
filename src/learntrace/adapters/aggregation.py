"""Deterministic grouping of privacy-filtered trace events.

The adapter layer deliberately keeps one ``ObservableEvent`` per observed
tool result.  This module adds a second, derived *shape* for those events:
``TraceWorkSegment``.  A segment is only an index over existing event IDs; it
is not a new evidence record and it never contains chat text or tool output.

The grouping rules are intentionally small and reproducible:

* a gap strictly greater than thirty minutes starts a new segment; and
* a clear switch between top-level project object scopes starts a new segment.

No model, network call, command execution, or Git relationship is involved.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from urllib.parse import unquote, urlparse

from learntrace.models import EventKind, ObservableEvent, SourceType
from learntrace.privacy import CommandCategory, classify_command

DEFAULT_SEGMENT_GAP = timedelta(minutes=30)
"""Default amount of idle time allowed inside one work segment."""

_TOOL_RE = re.compile(
    r"(?:OpenCode|Claude\s+Code|Codex)\s+(?:工具|tool)\s+(?P<tool>[A-Za-z0-9_.-]{1,64})",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"(?:路径[：:]|path\s*:\s*)(?P<path>[^。；\n，,]+)", re.IGNORECASE)
_COMMAND_RE = re.compile(
    r"(?:命令类型[：:]|command(?:\s+type)?\s*:\s*)(?P<command>[^。；\n]+)",
    re.IGNORECASE,
)
_SAFE_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_BOUNDARIES = frozenset({"time_gap", "object_switch"})
_AUTHORIZATIONS = frozenset({"minimal", "full"})
_SUMMARY_STATUSES = frozenset({"active", "denied"})
_COMMAND_CATEGORY_ALIASES = {
    "install_dependencies": "装依赖",
    "run_tests": "跑测试",
    "build_deploy": "构建部署",
    "inspect_files": "查文件",
    "other": "其他",
}


class SegmentBoundary(StrEnum):
    """Why a segment starts after the first segment."""

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


def _normalise_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise ValueError(f"{field_name} must be a sequence of strings")
    values = tuple(cast(Iterable[object], value))
    if not all(isinstance(item, str) and item for item in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return tuple(cast(str, item) for item in values)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _trace_source_metadata(event: ObservableEvent) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return safe host/session labels from trace URI refs and notes."""

    hosts: list[str] = []
    sessions: list[str] = []
    for source in event.source_refs:
        if source.type != SourceType.TRACE_RECORD:
            continue
        note = source.note
        if (
            isinstance(note, str)
            and note
            and note != "session-export"
            and _SAFE_EXTERNAL_ID_RE.fullmatch(note)
        ):
            hosts.append(note.casefold())
        parsed = urlparse(source.ref)
        if parsed.scheme != "trace":
            continue
        if parsed.netloc and _SAFE_EXTERNAL_ID_RE.fullmatch(parsed.netloc):
            hosts.append(parsed.netloc.casefold())
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parts and _SAFE_EXTERNAL_ID_RE.fullmatch(parts[0]):
            sessions.append(parts[0])
    return _ordered_unique(hosts), _ordered_unique(sessions)


def _extract_paths(summary: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _PATH_RE.finditer(summary):
        path = match.group("path").strip().rstrip(".,;:").replace("\\", "/")
        # These are safe placeholders, not project objects.  Treating them as
        # scopes would create arbitrary boundaries when an export is sanitized.
        if not path or path.startswith("["):
            continue
        paths.append(path)
    return _ordered_unique(paths)


def _path_scope(path: str) -> str | None:
    parts = [part for part in path.split("/") if part and part != "."]
    if not parts or ".." in parts:
        return None
    # A two-component path such as ``src/app.py`` should remain in the src
    # scope; otherwise every file in a source directory would form a segment.
    if len(parts) >= 3:
        return "/".join(parts[:2]).casefold()
    return parts[0].casefold()


def _extract_tool(summary: str) -> str | None:
    match = _TOOL_RE.search(summary)
    return match.group("tool").casefold() if match else None


def _extract_command_label(summary: str) -> str | None:
    match = _COMMAND_RE.search(summary)
    return match.group("command").strip().rstrip(".,;:") if match else None


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


@dataclass(frozen=True, slots=True)
class TraceWorkSegment:
    """A deterministic index over a contiguous set of trace event IDs.

    ``start_time`` and ``end_time`` are normalized UTC strings when at least
    one event in the segment has a valid timestamp.  ``None`` means the
    source did not provide a usable timestamp.  The aggregate lists contain
    only privacy-filtered labels already present in the event summaries.
    """

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
            raise ValueError("segment id must be a non-empty string")
        event_ids = _normalise_sequence(self.event_ids, "event_ids")
        if not event_ids:
            raise ValueError("event_ids must contain at least one trace event")
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("event_ids must be unique")
        object.__setattr__(self, "event_ids", event_ids)
        for field_name in (
            "tools",
            "paths",
            "command_categories",
            "source_hosts",
            "session_ids",
        ):
            value = _normalise_sequence(cast(Sequence[str], getattr(self, field_name)), field_name)
            if field_name == "command_categories":
                value = tuple(
                    _COMMAND_CATEGORY_ALIASES.get(item.casefold(), item) for item in value
                )
                invalid_categories = set(value) - {category.value for category in CommandCategory}
                if invalid_categories:
                    raise ValueError(
                        "unsupported command category: " + ", ".join(sorted(invalid_categories))
                    )
            object.__setattr__(self, field_name, _ordered_unique(value))
        if self.boundary_before not in (None, *_BOUNDARIES):
            raise ValueError(f"unsupported segment boundary: {self.boundary_before!r}")

    @property
    def segment_id(self) -> str:
        """Alias used by host-agent integrations."""

        return self.id

    @property
    def trace_event_ids(self) -> tuple[str, ...]:
        """Backward/forward-compatible alias for ``event_ids``."""

        return self.event_ids

    @property
    def time_range(self) -> dict[str, str | None]:
        return {"start": self.start_time, "end": self.end_time}

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "event_ids": list(self.event_ids),
            "time_range": self.time_range,
            "tools": list(self.tools),
            "paths": list(self.paths),
            "command_categories": list(self.command_categories),
            "source_hosts": list(self.source_hosts),
            "session_ids": list(self.session_ids),
        }
        if self.boundary_before is not None:
            data["boundary_before"] = self.boundary_before
        return data

    def to_agent_input(self, *, authorization: str = "minimal") -> dict[str, object]:
        """Project a segment into the fields the host Agent may use.

        The projection never contains raw summaries, commands, paths outside
        the project, chat text, or tool output.  ``full`` only tells the host
        that a separately authorized conversation may be re-read; it does not
        smuggle that conversation through this object.
        """

        if authorization not in _AUTHORIZATIONS:
            raise ValueError("authorization must be 'minimal' or 'full'")
        return {
            "segment_id": self.id,
            "event_ids": list(self.event_ids),
            "time_range": self.time_range,
            "tools": list(self.tools),
            "paths": list(self.paths),
            "command_categories": list(self.command_categories),
            "source_hosts": list(self.source_hosts),
            "authorization": authorization,
            "full_conversation_authorized": authorization == "full",
            **({"session_ids": list(self.session_ids)} if authorization == "full" else {}),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TraceWorkSegment:
        """Parse the additive archive representation.

        ``trace_event_ids`` and ``segment_id`` are accepted as aliases so a
        consumer can read early prototypes without changing the canonical
        output produced by :meth:`to_dict`.
        """

        def required_string(key: str, *aliases: str) -> str:
            value = raw.get(key)
            if value is None:
                for alias in aliases:
                    value = raw.get(alias)
                    if value is not None:
                        break
            if not isinstance(value, str) or not value:
                raise ValueError(f"work segment field {key!r} must be a non-empty string")
            return value

        def string_list(key: str, *aliases: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
            value: object = raw.get(key)
            if value is None:
                for alias in aliases:
                    value = raw.get(alias)
                    if value is not None:
                        break
            if value is None:
                return default
            if not isinstance(value, list):
                raise ValueError(f"work segment field {key!r} must be a list")
            return _normalise_sequence(cast(list[object], value), key)

        time_range = raw.get("time_range")
        start_time: str | None = None
        end_time: str | None = None
        if time_range is not None:
            if not isinstance(time_range, dict):
                raise ValueError("work segment field 'time_range' must be an object")
            time_range_data = cast(Mapping[str, object], time_range)
            start_raw = time_range_data.get("start")
            end_raw = time_range_data.get("end")
            if start_raw is not None and not isinstance(start_raw, str):
                raise ValueError("work segment time_range.start must be a string or null")
            if end_raw is not None and not isinstance(end_raw, str):
                raise ValueError("work segment time_range.end must be a string or null")
            start_time = start_raw
            end_time = end_raw
        else:
            start_raw = raw.get("start_time")
            end_raw = raw.get("end_time")
            if start_raw is not None and not isinstance(start_raw, str):
                raise ValueError("work segment start_time must be a string or null")
            if end_raw is not None and not isinstance(end_raw, str):
                raise ValueError("work segment end_time must be a string or null")
            start_time = start_raw
            end_time = end_raw
            coverage = raw.get("coverage")
            if coverage is not None:
                if not isinstance(coverage, str) or not coverage:
                    raise ValueError("work segment coverage must be a non-empty string")
                if start_time is None:
                    start_time = coverage
                if end_time is None:
                    end_time = coverage

        boundary = raw.get("boundary_before", raw.get("boundary_reason"))
        if boundary is not None and not isinstance(boundary, str):
            raise ValueError("work segment boundary must be a string or null")
        return cls(
            id=required_string("id", "segment_id"),
            event_ids=string_list("event_ids", "trace_event_ids", "citations"),
            start_time=start_time,
            end_time=end_time,
            tools=string_list("tools", "activity_shape"),
            paths=string_list("paths", "file_focus"),
            command_categories=string_list("command_categories"),
            source_hosts=string_list("source_hosts", "hosts"),
            session_ids=string_list("session_ids", "sessions"),
            boundary_before=boundary,
        )


# Names used in early Task 3 discussions; keep them as aliases rather than
# making downstream consumers guess which spelling is authoritative.
TraceSegment = TraceWorkSegment
WorkSegment = TraceWorkSegment


@dataclass(frozen=True, slots=True)
class TraceSegmentSummary:
    """A host-agent-written, deniable summary of one work segment.

    The deterministic adapter never creates this object.  It is an additive
    hand-off shape for a host Agent that has obtained the relevant consent.
    ``to_episode_dict`` matches the narrative payload's derived episode
    contract while keeping the segment ID available in the archive.
    """

    segment_id: str
    label: str
    body: str
    event_ids: tuple[str, ...]
    authorization: str = "minimal"
    status: str = "active"

    def __post_init__(self) -> None:
        for field_name in ("segment_id", "label", "body"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        object.__setattr__(self, "event_ids", _normalise_sequence(self.event_ids, "event_ids"))
        if not self.event_ids:
            raise ValueError("segment summary must cite at least one event")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("segment summary event_ids must be unique")
        if self.authorization not in _AUTHORIZATIONS:
            raise ValueError("summary authorization must be 'minimal' or 'full'")
        if self.status not in _SUMMARY_STATUSES:
            raise ValueError("summary status must be 'active' or 'denied'")

    @property
    def id(self) -> str:
        return f"summary-{self.segment_id}"

    @property
    def denied(self) -> bool:
        return self.status == "denied"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "segment_id": self.segment_id,
            "label": self.label,
            "body": self.body,
            "citations": list(self.event_ids),
            "event_ids": list(self.event_ids),
            "derived": True,
            "derivation": "host_agent_episode_digest",
            "deniable": True,
            "authorization": self.authorization,
            "status": self.status,
        }

    def to_episode_dict(self) -> dict[str, object]:
        """Return the exact derived-episode fields expected by narrative v0."""

        return {
            "label": self.label,
            "body": self.body,
            "citations": list(self.event_ids),
            "derived": True,
            "derivation": "host_agent_episode_digest",
            "deniable": True,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TraceSegmentSummary:
        def required_string(key: str, *aliases: str) -> str:
            value = raw.get(key)
            if value is None:
                for alias in aliases:
                    value = raw.get(alias)
                    if value is not None:
                        break
            if not isinstance(value, str) or not value:
                raise ValueError(f"segment summary field {key!r} must be a non-empty string")
            return value

        citations = raw.get("event_ids")
        if citations is None:
            citations = raw.get("citations")
        if not isinstance(citations, list):
            raise ValueError("segment summary citations must be a list")
        event_ids: list[str] = []
        for item in cast(list[object], citations):
            if isinstance(item, str):
                event_ids.append(item)
                continue
            if isinstance(item, dict):
                item_data = cast(Mapping[str, object], item)
                event_id = item_data.get("event_id")
                if isinstance(event_id, str):
                    event_ids.append(event_id)
                    continue
            raise ValueError("segment summary citations must contain event IDs")
        derived = raw.get("derived", True)
        if derived is not True:
            raise ValueError("segment summary must be marked derived=true")
        derivation = raw.get("derivation", "host_agent_episode_digest")
        if derivation != "host_agent_episode_digest":
            raise ValueError("unsupported segment summary derivation")
        deniable = raw.get("deniable", True)
        if deniable is not True:
            raise ValueError("segment summary must be deniable")
        authorization = raw.get("authorization", "minimal")
        status = raw.get("status", "active")
        if not isinstance(authorization, str) or not isinstance(status, str):
            raise ValueError("segment summary authorization/status must be strings")
        return cls(
            segment_id=required_string("segment_id", "id"),
            label=required_string("label"),
            body=required_string("body"),
            event_ids=tuple(event_ids),
            authorization=authorization,
            status=status,
        )


SegmentSummary = TraceSegmentSummary
WorkSegmentSummary = TraceSegmentSummary


def _facts_for_event(event: ObservableEvent) -> _TraceFacts:
    paths = _extract_paths(event.summary)
    scopes = _ordered_unique(scope for path in paths if (scope := _path_scope(path)) is not None)
    command_label = _extract_command_label(event.summary)
    category = classify_command(command_label) if command_label is not None else None
    hosts, sessions = _trace_source_metadata(event)
    return _TraceFacts(
        event=event,
        timestamp=_parse_timestamp(event.occurred_at),
        paths=paths,
        scopes=scopes,
        tool=_extract_tool(event.summary),
        command_category=category.value if isinstance(category, CommandCategory) else category,
        source_hosts=hosts,
        session_ids=sessions,
    )


def _facts_sort_key(item: tuple[int, _TraceFacts]) -> tuple[bool, str, str, str, int]:
    index, facts = item
    first_ref = facts.event.source_refs[0].ref if facts.event.source_refs else ""
    return (
        facts.timestamp is None,
        _timestamp_text(facts.timestamp) or "",
        first_ref,
        facts.event.id,
        index,
    )


def _object_switch(current_scopes: set[str], incoming_scopes: tuple[str, ...]) -> bool:
    if not current_scopes or not incoming_scopes:
        return False
    return current_scopes.isdisjoint(incoming_scopes)


def _segment_id(facts: Sequence[_TraceFacts]) -> str:
    content = "\0".join(fact.event.id for fact in facts)
    return f"segment-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"


def _build_segment(
    facts: Sequence[_TraceFacts],
    *,
    boundary_before: str | None,
) -> TraceWorkSegment:
    timestamps = [fact.timestamp for fact in facts if fact.timestamp is not None]
    tools = _ordered_unique(fact.tool for fact in facts if fact.tool is not None)
    paths = _ordered_unique(path for fact in facts for path in fact.paths)
    categories = _ordered_unique(
        fact.command_category for fact in facts if fact.command_category is not None
    )
    hosts = _ordered_unique(host for fact in facts for host in fact.source_hosts)
    sessions = _ordered_unique(session for fact in facts for session in fact.session_ids)
    return TraceWorkSegment(
        id=_segment_id(facts),
        event_ids=tuple(fact.event.id for fact in facts),
        start_time=_timestamp_text(min(timestamps) if timestamps else None),
        end_time=_timestamp_text(max(timestamps) if timestamps else None),
        tools=tools,
        paths=paths,
        command_categories=categories,
        source_hosts=hosts,
        session_ids=sessions,
        boundary_before=boundary_before,
    )


def segment_trace_events(
    events: Iterable[ObservableEvent],
    *,
    gap_threshold: timedelta = DEFAULT_SEGMENT_GAP,
    gap_minutes: float | None = None,
) -> tuple[TraceWorkSegment, ...]:
    """Group trace events into deterministic work segments.

    Non-trace events are ignored.  Events with invalid or missing timestamps
    never cause a time-gap split; they remain grouped by the object rule.  A
    gap exactly equal to the threshold remains in the same segment, while a
    strictly larger gap starts the next one.
    """

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
            if existing[1].to_dict() != event.to_dict():
                raise ValueError(f"conflicting trace events for id {event.id}")
            continue
        by_id[event.id] = (index, event)

    ordered = sorted(
        ((index, _facts_for_event(event)) for index, event in by_id.values()),
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
            if boundary is None and _object_switch(current_scopes, incoming.scopes):
                boundary = SegmentBoundary.OBJECT_SWITCH.value
        if boundary is not None:
            segments.append(_build_segment(current, boundary_before=pending_boundary))
            current = []
            current_scopes = set()
            pending_boundary = boundary
        current.append(incoming)
        current_scopes.update(incoming.scopes)
        # Assigning None here deliberately prevents a gap from being inferred
        # across an unknown timestamp; the next usable timestamp is not
        # adjacent to a known point in time.
        previous_time = incoming.timestamp
    if current:
        segments.append(_build_segment(current, boundary_before=pending_boundary))
    return tuple(segments)


# Public spelling used by a few consumers and by the Issue #28 discussion.
aggregate_trace_events = segment_trace_events
build_work_segments = segment_trace_events


def validate_work_segments(
    segments: Iterable[TraceWorkSegment],
    events: Iterable[ObservableEvent],
) -> None:
    """Check that segment references stay inside the observable trace set."""

    event_map = {event.id: event for event in events}
    seen: set[str] = set()
    for segment in segments:
        if segment.id in seen:
            raise ValueError(f"duplicate work segment id: {segment.id}")
        seen.add(segment.id)
        for event_id in segment.event_ids:
            event = event_map.get(event_id)
            if event is None:
                raise ValueError(f"{segment.id}: missing trace event {event_id}")
            if event.kind != EventKind.TRACE_RECORD:
                raise ValueError(f"{segment.id}: event {event_id} is not a trace_record")


def validate_segment_summaries(
    summaries: Iterable[TraceSegmentSummary],
    segments: Iterable[TraceWorkSegment],
    events: Iterable[ObservableEvent],
) -> None:
    """Check summary citations and segment ownership without reading content."""

    segment_map = {segment.id: segment for segment in segments}
    event_map = {event.id: event for event in events}
    seen: set[str] = set()
    for summary in summaries:
        if summary.segment_id in seen:
            raise ValueError(f"duplicate segment summary for {summary.segment_id}")
        seen.add(summary.segment_id)
        segment = segment_map.get(summary.segment_id)
        if segment is None:
            raise ValueError(f"summary references missing work segment {summary.segment_id}")
        for event_id in summary.event_ids:
            if event_id not in event_map:
                raise ValueError(
                    f"summary for {summary.segment_id} references missing event {event_id}"
                )
            if event_id not in segment.event_ids:
                raise ValueError(
                    f"summary for {summary.segment_id} cites event outside its segment: {event_id}"
                )


__all__ = [
    "DEFAULT_SEGMENT_GAP",
    "SegmentBoundary",
    "TraceSegment",
    "TraceSegmentSummary",
    "TraceWorkSegment",
    "WorkSegment",
    "WorkSegmentSummary",
    "SegmentSummary",
    "aggregate_trace_events",
    "build_work_segments",
    "segment_trace_events",
    "validate_segment_summaries",
    "validate_work_segments",
]
