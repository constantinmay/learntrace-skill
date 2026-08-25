"""Privacy-minimizing adapter for a caller-provided Codex CLI session file."""

from __future__ import annotations

import hashlib
import json
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
)
from learntrace.models import (
    ContractValidator,
    EventKind,
    ObservableEvent,
    SourceRef,
    SourceType,
)
from learntrace.privacy import (
    redact_sensitive_text,
    summarize_command,
)

_MAX_EVENTS_PER_SESSION = 2000
_MAX_TRACKED_CALLS = 2000
_MAX_ARGUMENT_BYTES = 64 * 1024
_LINE_MARKERS = ("function_call", "local_shell_call", "session_meta", "custom_tool_call")
_CALL_PAYLOAD_TYPES = frozenset({"function_call", "local_shell_call"})
_UNSUPPORTED_CALL_TYPES = frozenset({"custom_tool_call", "custom_tool_call_output"})
_SAFE_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")
_EXIT_STATUS_TEXT_RE = re.compile(r"(?:exit code|exited with code)\s*:?\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _PendingCall:
    call_id: str
    tool: str
    command: str | None
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


def _summary(*, tool: str, is_error: bool | None, command: str | None) -> str:
    safe_tool = _safe_tool_name(tool)
    if is_error is True:
        summary = f"Codex 工具 {safe_tool} 以错误结束。"
    elif is_error is None:
        summary = f"Codex 工具 {safe_tool} 结束（状态未知）。"
    else:
        summary = f"Codex 工具 {safe_tool} 已完成。"
    if command is not None:
        summary += f" 命令类型：{summarize_command(command)}。"
    return redact_sensitive_text(summary)


def _joined_command_tokens(command: object) -> str | None:
    if not isinstance(command, list):
        return None
    tokens: list[str] = []
    for token in cast("list[object]", command):
        if not isinstance(token, str):
            return None
        tokens.append(token)
    return " ".join(tokens) if tokens else None


def _exceeds_argument_cap(value: str) -> bool:
    """Return True when ``value`` exceeds the byte cap once UTF-8 encoded.

    The character count bounds the byte length between ``len(value)`` and
    ``4 * len(value)``, so only the ambiguous middle range pays for an actual
    encoding pass.
    """

    if len(value) > _MAX_ARGUMENT_BYTES:
        return True
    if len(value) * 4 <= _MAX_ARGUMENT_BYTES:
        return False
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        # Lone surrogates (e.g. from JSON "\ud800" escapes) cannot encode;
        # treat the field as over the cap instead of aborting the session.
        return True
    return encoded_length > _MAX_ARGUMENT_BYTES


def _command_from_arguments(value: object) -> str | None:
    """Extract a conservative command label from JSON-encoded call arguments.

    Oversized argument blobs are never parsed; they usually embed full file
    contents that must not reach the parser in the first place.
    """

    if not isinstance(value, str) or not value or _exceeds_argument_cap(value):
        return None
    try:
        parsed: object = json.loads(value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict):
        return None
    arguments = cast("dict[str, object]", parsed)
    command = arguments.get("command")
    if isinstance(command, str):
        return command
    cmd = arguments.get("cmd")
    if isinstance(cmd, str):
        return cmd
    return _joined_command_tokens(command)


def _command_from_action(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    action = cast("dict[str, object]", value)
    command = action.get("command")
    if isinstance(command, str):
        return command
    return _joined_command_tokens(command)


def _text_exit_status(text: str) -> bool | None:
    match = _EXIT_STATUS_TEXT_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)) != 0


def _output_exit_status(value: object) -> bool | None:
    """Read only the exit status of a call output; its content is never kept.

    Returns ``True`` for an observed non-zero exit, ``False`` for an observed
    zero exit, and ``None`` when the status cannot be confirmed. An unknown
    outcome is never silently reported as success.
    """

    if not isinstance(value, str) or not value or _exceeds_argument_cap(value):
        return None
    try:
        parsed: object = json.loads(value)
    except (ValueError, RecursionError):
        return _text_exit_status(value)
    if not isinstance(parsed, dict):
        return _text_exit_status(value)
    data = cast("dict[str, object]", parsed)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        exit_code = cast("dict[str, object]", metadata).get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            return exit_code != 0
    top_level_exit_code = data.get("exit_code")
    if isinstance(top_level_exit_code, int) and not isinstance(top_level_exit_code, bool):
        return top_level_exit_code != 0
    output = data.get("output")
    if isinstance(output, str):
        return _text_exit_status(output)
    return None


def _source_ref(session_id: str, call_id: str) -> str:
    return f"trace://codex/{quote(session_id, safe='')}/response_item/{quote(call_id, safe='')}"


def _event_sort_key(event: ObservableEvent) -> tuple[bool, str, str]:
    return (
        event.occurred_at is None,
        event.occurred_at or "",
        event.source_refs[0].ref,
    )


def _session_from_meta(record: dict[str, object]) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    candidate = cast("dict[str, object]", payload).get("id")
    return candidate if _is_safe_external_id(candidate) else None


def _pending_from_payload(
    payload: dict[str, object],
    *,
    location: str,
    occurred_at: str | None,
    warnings: WarningLog,
) -> _PendingCall | None:
    payload_type = payload.get("type")
    if payload_type == "function_call":
        call_id = payload.get("call_id")
        tool = payload.get("name")
        if not _is_safe_external_id(call_id) or not isinstance(tool, str) or not tool:
            warnings.add("invalid_tool_call", location, "工具记录结构无效，已跳过。")
            return None
        return _PendingCall(
            call_id=call_id,
            tool=tool,
            command=_command_from_arguments(payload.get("arguments")),
            occurred_at=occurred_at,
            location=location,
        )
    if payload_type == "local_shell_call":
        call_id = payload.get("call_id")
        if not _is_safe_external_id(call_id):
            warnings.add("invalid_tool_call", location, "工具记录结构无效，已跳过。")
            return None
        return _PendingCall(
            call_id=call_id,
            tool="local_shell",
            command=_command_from_action(payload.get("action")),
            occurred_at=occurred_at,
            location=location,
        )
    return None


def _sorted_issues(issues: list[TraceParseIssue]) -> list[TraceParseIssue]:
    return sorted(issues, key=lambda issue: (issue.location, issue.code, issue.message))


def adapt_codex_export(
    export_path: Path | None,
    *,
    authorized: bool,
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Adapt one explicitly authorized Codex JSONL session into v0 events.

    A call becomes an event only when its output item arrives, so the recorded
    status is observed rather than inferred. Output payloads are never copied;
    only a compact exit-status signal is read. When the exit status cannot be
    confirmed the event is recorded with an unknown status instead of being
    reported as success. Parse-phase memory is bounded: event construction,
    warning collection and unmatched-call tracking all stop at their caps with
    a summary warning.
    """

    del project_root  # Codex call arguments carry no project-relative paths yet.
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
    seen_calls: set[tuple[str, str]] = set()
    resolved_session: str | None = None
    dropped_events = 0
    dropped_calls = 0

    for line_number, record in iter_jsonl_records(
        export_path,
        label="Codex",
        markers=_LINE_MARKERS,
        warnings=warnings,
    ):
        location = f"lines[{line_number - 1}]"
        record_type = record.get("type")
        if record_type == "session_meta" and resolved_session is None:
            meta_session = _session_from_meta(record)
            if meta_session is not None:
                resolved_session = meta_session
            continue

        payload = record.get("payload")
        if not isinstance(payload, dict):
            if record_type == "response_item":
                warnings.add("invalid_record", location, "记录结构无效，已跳过。")
            continue
        payload_data = cast("dict[str, object]", payload)
        payload_type = payload_data.get("type")

        if isinstance(payload_type, str) and payload_type in _UNSUPPORTED_CALL_TYPES:
            warnings.add(
                "unsupported_call_type",
                location,
                "该调用类型暂不支持导入，已跳过。",
            )
            continue

        if isinstance(payload_type, str) and payload_type in _CALL_PAYLOAD_TYPES:
            if len(seen_calls) >= _MAX_TRACKED_CALLS:
                dropped_calls += 1
                continue
            if resolved_session is None:
                stem = export_path.stem
                if not _is_safe_external_id(stem):
                    raise UnsupportedTraceFormatError("Codex 会话缺少可用的会话标识。")
                resolved_session = stem
            pending_call = _pending_from_payload(
                payload_data,
                location=location,
                occurred_at=_occurred_at(
                    record.get("timestamp"),
                    location=location,
                    warnings=warnings,
                ),
                warnings=warnings,
            )
            if pending_call is None:
                continue
            identity = (resolved_session, pending_call.call_id)
            if identity in seen_calls:
                warnings.add("duplicate_tool_call", location, "重复工具记录已跳过。")
                continue
            seen_calls.add(identity)
            pending[pending_call.call_id] = pending_call
            continue

        if payload_type == "function_call_output":
            call_id = payload_data.get("call_id")
            if not _is_safe_external_id(call_id):
                warnings.add("invalid_tool_result", location, "工具结果记录结构无效，已跳过。")
                continue
            pending_call = pending.pop(call_id, None)
            if pending_call is None:
                warnings.add("orphan_tool_result", location, "未找到对应工具调用的结果，已跳过。")
                continue
            if len(events) >= _MAX_EVENTS_PER_SESSION:
                dropped_events += 1
                continue
            if resolved_session is None:
                stem = export_path.stem
                if not _is_safe_external_id(stem):
                    raise UnsupportedTraceFormatError("Codex 会话缺少可用的会话标识。")
                resolved_session = stem
            is_error = _output_exit_status(payload_data.get("output"))
            source_ref = _source_ref(resolved_session, pending_call.call_id)
            event = ObservableEvent(
                id=f"evt-trace-{hashlib.sha256(source_ref.encode()).hexdigest()[:16]}",
                kind=EventKind.TRACE_RECORD,
                summary=_summary(
                    tool=pending_call.tool,
                    is_error=is_error,
                    command=pending_call.command,
                ),
                source_refs=(
                    SourceRef(
                        type=SourceType.TRACE_RECORD,
                        ref=source_ref,
                        note="codex",
                    ),
                ),
                occurred_at=pending_call.occurred_at,
            )
            event_validator.validate("observable_event", event.to_dict())
            events.append(event)

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


def adapt_codex_exports(
    export_paths: tuple[Path, ...],
    *,
    authorized_paths: tuple[Path, ...],
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Merge multiple explicitly authorized Codex session files.

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
                "该 Codex 会话文件未获单独授权，未读取。",
            )
            continue
        any_authorized = True
        result = adapt_codex_export(
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
            if existing.to_dict() != event.to_dict():
                raise UnsupportedTraceFormatError(
                    "多个 Codex 会话包含 ID 相同但内容冲突的工具记录。"
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


__all__ = ["adapt_codex_export", "adapt_codex_exports"]
