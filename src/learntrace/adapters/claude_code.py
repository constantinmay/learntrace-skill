"""Privacy-minimizing adapter for a caller-provided Claude Code session file."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard, cast
from urllib.parse import quote

from learntrace.adapters._jsonl import WarningLog, iter_jsonl_records
from learntrace.adapters.types import (
    TraceAdapterResult,
    TraceInputStatus,
    TraceParseIssue,
    UnsupportedTraceFormatError,
    events_conflict,
)
from learntrace.models import (
    ContractValidator,
    EventKind,
    ObservableEvent,
    SourceRef,
    SourceType,
)
from learntrace.privacy import (
    normalize_project_path,
    redact_sensitive_text,
    summarize_command,
)

_MAX_EVENTS_PER_SESSION = 2000
_MAX_TRACKED_CALLS = 2000
_FILE_TOOLS = frozenset({"read", "write", "edit", "notebookedit"})
_KNOWN_BLOCK_TYPES = frozenset({"text", "thinking", "redacted_thinking", "tool_result", "image"})
_LINE_MARKERS = ("tool_use", "tool_result")
_SAFE_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


@dataclass(frozen=True, slots=True)
class _PendingCall:
    tool_use_id: str
    message_id: str
    tool: str
    input_data: dict[str, object]
    occurred_at: str | None
    location: str


def _is_safe_external_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SAFE_EXTERNAL_ID_RE.fullmatch(value) is not None


def _safe_tool_name(value: str) -> str:
    if _SAFE_TOOL_RE.fullmatch(value):
        return value.casefold()
    return "unknown-tool"


def _occurred_at(
    value: object,
    *,
    location: str,
    warnings: WarningLog,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.add("invalid_timestamp", location, "工具开始时间无效，已省略。")
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        timestamp = datetime.fromisoformat(text)
    except ValueError:
        warnings.add("invalid_timestamp", location, "工具开始时间无效，已省略。")
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _summary(
    *,
    tool: str,
    is_error: bool,
    input_data: dict[str, object],
    project_root: Path | None,
) -> str:
    safe_tool = _safe_tool_name(tool)
    if is_error:
        summary = f"Claude Code 工具 {safe_tool} 以错误结束。"
    else:
        summary = f"Claude Code 工具 {safe_tool} 已完成。"

    if safe_tool == "bash":
        command = input_data.get("command")
        if isinstance(command, str):
            summary += f" 命令类型：{summarize_command(command)}。"
    elif safe_tool in _FILE_TOOLS:
        path_value = input_data.get("file_path")
        if not isinstance(path_value, str):
            path_value = input_data.get("path")
        if isinstance(path_value, str):
            summary += f" 路径：{normalize_project_path(path_value, project_root)}。"

    return redact_sensitive_text(summary)


def _source_ref(session_id: str, message_id: str, tool_use_id: str) -> str:
    return (
        f"trace://claude-code/{quote(session_id, safe='')}"
        f"/message/{quote(message_id, safe='')}/tool_use/{quote(tool_use_id, safe='')}"
    )


def _event_sort_key(event: ObservableEvent) -> tuple[bool, str, str]:
    return (
        event.occurred_at is None,
        event.occurred_at or "",
        event.source_refs[0].ref,
    )


def _sorted_issues(issues: list[TraceParseIssue]) -> list[TraceParseIssue]:
    return sorted(issues, key=lambda issue: (issue.location, issue.code, issue.message))


def _resolve_session_id(
    record: dict[str, object],
    resolved: str | None,
    *,
    export_path: Path,
) -> str:
    if resolved is not None:
        return resolved
    candidate = record.get("sessionId")
    if _is_safe_external_id(candidate):
        return candidate
    stem = export_path.stem
    if _is_safe_external_id(stem):
        return stem
    raise UnsupportedTraceFormatError("Claude Code 会话缺少可用的会话标识。")


def _collect_tool_calls(
    record: dict[str, object],
    line_number: int,
    *,
    session_id: str,
    warnings: WarningLog,
    pending: dict[str, _PendingCall],
    seen_calls: set[tuple[str, str, str]],
) -> int:
    """Collect tool calls from one assistant record.

    Returns the number of calls dropped because the tracked-call cap was
    already reached, so parse-phase memory stays bounded.
    """

    line_location = f"lines[{line_number - 1}]"
    message_value = record.get("message")
    if not isinstance(message_value, dict):
        warnings.add("invalid_record", line_location, "记录结构无效，已跳过。")
        return 0
    message = cast("dict[str, object]", message_value)
    message_id = record.get("uuid")
    content_value = message.get("content")
    if not _is_safe_external_id(message_id) or not isinstance(content_value, list):
        warnings.add("invalid_record", line_location, "记录结构无效，已跳过。")
        return 0
    content = cast("list[object]", content_value)
    timestamp = _occurred_at(
        record.get("timestamp"),
        location=line_location,
        warnings=warnings,
    )

    dropped = 0
    for block_index, block_value in enumerate(content):
        block_location = f"{line_location}.content[{block_index}]"
        if not isinstance(block_value, dict):
            warnings.add("invalid_tool_call", block_location, "工具记录结构无效，已跳过。")
            continue
        block = cast("dict[str, object]", block_value)
        block_type = block.get("type")
        if block_type != "tool_use":
            if isinstance(block_type, str) and block_type in _KNOWN_BLOCK_TYPES:
                continue
            warnings.add("unknown_block_type", block_location, "未知记录类型，已跳过。")
            continue

        tool_use_id = block.get("id")
        tool = block.get("name")
        input_value = block.get("input")
        if (
            not _is_safe_external_id(tool_use_id)
            or not isinstance(tool, str)
            or not tool
            or not isinstance(input_value, dict)
        ):
            warnings.add("invalid_tool_call", block_location, "工具记录结构无效，已跳过。")
            continue

        if len(seen_calls) >= _MAX_TRACKED_CALLS:
            dropped += 1
            continue
        identity = (session_id, message_id, tool_use_id)
        if identity in seen_calls:
            warnings.add("duplicate_tool_call", block_location, "重复工具记录已跳过。")
            continue
        seen_calls.add(identity)
        pending[tool_use_id] = _PendingCall(
            tool_use_id=tool_use_id,
            message_id=message_id,
            tool=tool,
            input_data=cast("dict[str, object]", input_value),
            occurred_at=timestamp,
            location=block_location,
        )
    return dropped


def _matched_tool_results(
    record: dict[str, object],
    line_number: int,
    *,
    warnings: WarningLog,
    pending: dict[str, _PendingCall],
) -> list[tuple[_PendingCall, bool]]:
    line_location = f"lines[{line_number - 1}]"
    message_value = record.get("message")
    if not isinstance(message_value, dict):
        return []
    message = cast("dict[str, object]", message_value)
    content_value = message.get("content")
    if not isinstance(content_value, list):
        return []

    matched: list[tuple[_PendingCall, bool]] = []
    for block_value in cast("list[object]", content_value):
        if not isinstance(block_value, dict):
            continue
        block = cast("dict[str, object]", block_value)
        if block.get("type") != "tool_result":
            continue
        tool_use_id = block.get("tool_use_id")
        if not _is_safe_external_id(tool_use_id):
            warnings.add("invalid_tool_result", line_location, "工具结果记录结构无效，已跳过。")
            continue
        pending_call = pending.pop(tool_use_id, None)
        if pending_call is None:
            warnings.add("orphan_tool_result", line_location, "未找到对应工具调用的结果，已跳过。")
            continue
        matched.append((pending_call, block.get("is_error") is True))
    return matched


def _build_event(
    pending_call: _PendingCall,
    *,
    is_error: bool,
    session_id: str,
    export_path: Path,
    project_root: Path | None,
    validator: ContractValidator,
) -> ObservableEvent:
    source_ref = _source_ref(session_id, pending_call.message_id, pending_call.tool_use_id)
    event = ObservableEvent(
        id=f"evt-trace-{hashlib.sha256(source_ref.encode()).hexdigest()[:16]}",
        kind=EventKind.TRACE_RECORD,
        summary=_summary(
            tool=pending_call.tool,
            is_error=is_error,
            input_data=pending_call.input_data,
            project_root=project_root,
        ),
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref=source_ref,
                note="claude-code",
            ),
            SourceRef(
                type=SourceType.FILE,
                ref=export_path.resolve().as_posix(),
                note="session-export",
            ),
        ),
        occurred_at=pending_call.occurred_at,
    )
    validator.validate("observable_event", event.to_dict())
    return event


def adapt_claude_code_export(
    export_path: Path | None,
    *,
    authorized: bool,
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Adapt one explicitly authorized Claude Code JSONL session into v0 events.

    A tool call becomes an event only when its ``tool_result`` arrives, so the
    recorded status is observed rather than inferred. Result payloads are never
    copied; only the ``is_error`` flag is read. Parse-phase memory is bounded:
    event construction, warning collection and unmatched-call tracking all
    stop at their caps with a summary warning.
    """

    if not authorized:
        return TraceAdapterResult(status=TraceInputStatus.NOT_AUTHORIZED)
    if export_path is None:
        return TraceAdapterResult(status=TraceInputStatus.NOT_PROVIDED)
    try:
        is_file = export_path.is_file()
    except OSError:
        is_file = False
    if not is_file:
        return TraceAdapterResult(status=TraceInputStatus.AUTHORIZED_NOT_FOUND)

    warnings = WarningLog()
    event_validator = validator if validator is not None else ContractValidator()
    events: list[ObservableEvent] = []
    pending: dict[str, _PendingCall] = {}
    seen_calls: set[tuple[str, str, str]] = set()
    resolved_session: str | None = None
    dropped_events = 0
    dropped_calls = 0

    for line_number, record in iter_jsonl_records(
        export_path,
        label="Claude Code",
        markers=_LINE_MARKERS,
        warnings=warnings,
    ):
        record_type = record.get("type")
        if record_type not in ("assistant", "user"):
            continue
        resolved_session = _resolve_session_id(
            record,
            resolved_session,
            export_path=export_path,
        )
        if record_type == "assistant":
            dropped_calls += _collect_tool_calls(
                record,
                line_number,
                session_id=resolved_session,
                warnings=warnings,
                pending=pending,
                seen_calls=seen_calls,
            )
            continue
        for pending_call, is_error in _matched_tool_results(
            record,
            line_number,
            warnings=warnings,
            pending=pending,
        ):
            if len(events) >= _MAX_EVENTS_PER_SESSION:
                dropped_events += 1
                continue
            events.append(
                _build_event(
                    pending_call,
                    is_error=is_error,
                    session_id=resolved_session,
                    export_path=export_path,
                    project_root=project_root,
                    validator=event_validator,
                )
            )

    for unmatched in sorted(pending.values(), key=lambda call: call.location):
        warnings.add(
            "incomplete_tool_call",
            unmatched.location,
            "工具调用未观察到结果记录，已跳过。",
        )
    if dropped_events:
        warnings.add_summary(
            "event_cap_reached",
            "events",
            (
                f"单会话轨迹事件达到 {_MAX_EVENTS_PER_SESSION} 条上限，"
                f"其余 {dropped_events} 条工具记录未导入。"
            ),
        )
    if dropped_calls:
        warnings.add_summary(
            "tracked_call_cap_reached",
            "pending",
            (
                f"未配对工具调用跟踪达到 {_MAX_TRACKED_CALLS} 条上限，"
                f"其余 {dropped_calls} 条调用记录未导入。"
            ),
        )

    events.sort(key=_event_sort_key)
    issues = _sorted_issues(warnings.finalize())
    result_status = TraceInputStatus.PARSED if events else TraceInputStatus.AUTHORIZED_NOT_FOUND
    return TraceAdapterResult(
        status=result_status,
        events=tuple(events),
        warnings=tuple(issues),
    )


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=False)))
    except OSError:
        return os.path.normcase(str(path.absolute()))


def adapt_claude_code_exports(
    export_paths: tuple[Path, ...],
    *,
    authorized_paths: tuple[Path, ...],
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Merge multiple explicitly authorized Claude Code session files.

    Authorization is matched per path before a file is opened. Repeating an
    overlapping session is safe: identical events are retained once while their
    session-bearing ``trace://`` provenance remains on the event.
    """

    if not export_paths:
        return TraceAdapterResult(status=TraceInputStatus.NOT_PROVIDED)

    authorized_keys = {_path_key(path) for path in authorized_paths}
    if not authorized_keys:
        return TraceAdapterResult(status=TraceInputStatus.NOT_AUTHORIZED)

    events_by_id: dict[str, ObservableEvent] = {}
    warnings = WarningLog()
    any_authorized = False
    for export_index, export_path in enumerate(export_paths):
        prefix = f"exports[{export_index}]"
        if _path_key(export_path) not in authorized_keys:
            warnings.add(
                "export_not_authorized",
                prefix,
                "该 Claude Code 会话文件未获单独授权，未读取。",
            )
            continue
        any_authorized = True
        result = adapt_claude_code_export(
            export_path,
            authorized=True,
            project_root=project_root,
            validator=validator,
        )
        warnings.extend(
            TraceParseIssue(
                code=issue.code,
                location=f"{prefix}.{issue.location}",
                message=issue.message,
            )
            for issue in result.warnings
        )
        duplicate_count = 0
        for event in result.events:
            existing = events_by_id.get(event.id)
            if existing is None:
                events_by_id[event.id] = event
                continue
            if events_conflict(existing, event):
                raise UnsupportedTraceFormatError(
                    "多个 Claude Code 会话包含 ID 相同但内容冲突的工具记录。"
                )
            duplicate_count += 1
        if duplicate_count:
            warnings.add(
                "duplicate_export_event",
                prefix,
                f"与先前会话重叠的 {duplicate_count} 条工具记录已去重。",
            )

    if not any_authorized:
        return TraceAdapterResult(
            status=TraceInputStatus.NOT_AUTHORIZED,
            warnings=tuple(_sorted_issues(warnings.finalize())),
        )
    events = tuple(sorted(events_by_id.values(), key=_event_sort_key))
    return TraceAdapterResult(
        status=(TraceInputStatus.PARSED if events else TraceInputStatus.AUTHORIZED_NOT_FOUND),
        events=events,
        warnings=tuple(_sorted_issues(warnings.finalize())),
    )


__all__ = ["adapt_claude_code_export", "adapt_claude_code_exports"]
