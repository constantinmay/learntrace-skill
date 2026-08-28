"""Privacy-minimizing adapter for a caller-provided OpenCode JSON export."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeGuard, cast
from urllib.parse import quote

from learntrace.adapters.types import (
    TraceAdapterResult,
    TraceInputStatus,
    TraceParseIssue,
    UnsupportedOpenCodeFormatError,
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

_MAX_EXPORT_BYTES = 32 * 1024 * 1024
# Reject pathologically deep JSON nesting before handing it to json.loads: the C
# accelerator can parse hundreds of bracket levels without hitting the Python
# recursion limit, so a RecursionError alone is not a reliable guard.
_MAX_JSON_NESTING_DEPTH = 128
_SUPPORTED_STATUS = frozenset({"pending", "running", "completed", "error"})
_INCOMPLETE_STATUS = frozenset({"pending", "running"})
_FILE_TOOLS = frozenset({"read", "edit", "write", "apply_patch"})
_KNOWN_NON_TOOL_PARTS = frozenset(
    {
        "text",
        "reasoning",
        "file",
        "snapshot",
        "patch",
        "step-start",
        "step-finish",
        "subtask",
        "retry",
        "compaction",
    }
)
_SAFE_TOOL_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_EXTERNAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


def _issue(code: str, location: str, message: str) -> TraceParseIssue:
    return TraceParseIssue(code=code, location=location, message=message)


def _max_nesting_depth(text: str) -> int:
    """Return the deepest bracket nesting, honoring string literals.

    A single forward pass over the raw text tracks ``{[`` pairs (and marks
    strings so braces inside JSON strings are not counted). This is cheaper and
    more robust than relying on json.loads recursion depth to reject hostile
    input.
    """

    depth = 0
    max_depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            max_depth = max(max_depth, depth)
        elif char in "]}":
            depth -= 1
    return max_depth


def _load_export(export_path: Path) -> dict[str, object]:
    try:
        size = export_path.stat().st_size
    except OSError as exc:  # pragma: no cover - guarded by is_file in the public function
        raise UnsupportedOpenCodeFormatError("无法读取 OpenCode 导出文件。") from exc
    if size > _MAX_EXPORT_BYTES:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出文件超过 32 MiB 限制。")

    try:
        raw_bytes = export_path.read_bytes()
    except OSError as exc:
        raise UnsupportedOpenCodeFormatError("无法读取 OpenCode 导出文件。") from exc
    if len(raw_bytes) > _MAX_EXPORT_BYTES:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出文件超过 32 MiB 限制。")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出文件不是有效 UTF-8。") from exc
    if _max_nesting_depth(text) > _MAX_JSON_NESTING_DEPTH:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出文件嵌套过深。")
    try:
        parsed: object = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出文件不是有效 JSON。") from exc
    if not isinstance(parsed, dict):
        raise UnsupportedOpenCodeFormatError("OpenCode 导出的根结构必须是对象。")
    return cast("dict[str, object]", parsed)


def _batch_fields(root: dict[str, object]) -> tuple[str, str, list[object]]:
    info_value = root.get("info")
    messages_value = root.get("messages")
    if not isinstance(info_value, dict) or not isinstance(messages_value, list):
        raise UnsupportedOpenCodeFormatError("OpenCode 导出缺少必需的 info 或 messages。")
    info = cast("dict[str, object]", info_value)
    session_id = info.get("id")
    version = info.get("version")
    if not _is_safe_external_id(session_id) or not isinstance(version, str) or not version:
        raise UnsupportedOpenCodeFormatError("OpenCode 导出的 info 字段不兼容。")
    return session_id, version, cast("list[object]", messages_value)


def _is_safe_external_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and _SAFE_EXTERNAL_ID_RE.fullmatch(value) is not None


def _safe_tool_name(value: str) -> str:
    if _SAFE_TOOL_RE.fullmatch(value):
        return value.casefold()
    return "unknown-tool"


def _source_ref(session_id: str, message_id: str, part_id: str) -> str:
    return (
        f"trace://opencode/{quote(session_id, safe='')}"
        f"/message/{quote(message_id, safe='')}/part/{quote(part_id, safe='')}"
    )


def _occurred_at(
    state: dict[str, object],
    *,
    location: str,
    warnings: list[TraceParseIssue],
) -> str | None:
    time_value = state.get("time")
    if time_value is None:
        return None
    if not isinstance(time_value, dict):
        warnings.append(_issue("invalid_timestamp", location, "工具开始时间无效，已省略。"))
        return None
    time_data = cast("dict[str, object]", time_value)
    if "start" not in time_data:
        return None
    start_value = time_data["start"]
    if isinstance(start_value, bool) or not isinstance(start_value, (int, float)):
        warnings.append(_issue("invalid_timestamp", location, "工具开始时间无效，已省略。"))
        return None
    try:
        start = float(start_value)
        if not math.isfinite(start) or start < 0:
            raise ValueError
        timestamp = datetime.fromtimestamp(start / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        warnings.append(_issue("invalid_timestamp", location, "工具开始时间无效，已省略。"))
        return None
    return timestamp.isoformat().replace("+00:00", "Z")


def _summary(
    *,
    tool: str,
    status: str,
    input_data: dict[str, object],
    persisted_message_error: bool,
    project_root: Path | None,
) -> str:
    safe_tool = _safe_tool_name(tool)
    if status == "completed":
        summary = f"OpenCode 工具 {safe_tool} 已完成。"
    elif status == "error":
        summary = f"OpenCode 工具 {safe_tool} 以错误结束。"
    elif persisted_message_error:
        summary = f"OpenCode 工具 {safe_tool} 在消息错误结束时未完成。"
    else:  # pragma: no cover - incomplete live records are skipped before this helper
        summary = f"OpenCode 工具 {safe_tool} 状态未记录。"

    if safe_tool == "bash":
        command = input_data.get("command")
        if isinstance(command, str):
            summary += f" 命令类型：{summarize_command(command)}。"
    elif safe_tool in _FILE_TOOLS:
        path_value = input_data.get("filePath")
        if not isinstance(path_value, str):
            path_value = input_data.get("path")
        if isinstance(path_value, str):
            summary += f" 路径：{normalize_project_path(path_value, project_root)}。"

    return redact_sensitive_text(summary)


def _event_sort_key(event: ObservableEvent) -> tuple[bool, str, str]:
    return (
        event.occurred_at is None,
        event.occurred_at or "",
        event.source_refs[0].ref,
    )


def adapt_opencode_export(
    export_path: Path | None,
    *,
    authorized: bool,
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Adapt one explicitly authorized OpenCode JSON export into v0 events."""

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

    root = _load_export(export_path)
    session_id, version, messages = _batch_fields(root)
    warnings: list[TraceParseIssue] = []
    if version.split(".", maxsplit=1)[0] != "1":
        warnings.append(
            _issue(
                "unverified_opencode_version",
                "info.version",
                "OpenCode 应用版本未经 M1 样例验证；已按兼容结构解析。",
            )
        )

    event_validator = validator if validator is not None else ContractValidator()
    events: list[ObservableEvent] = []
    seen_parts: set[tuple[str, str, str]] = set()

    for message_index, message_value in enumerate(messages):
        message_location = f"messages[{message_index}]"
        if not isinstance(message_value, dict):
            warnings.append(_issue("invalid_message", message_location, "消息结构无效，已跳过。"))
            continue
        message = cast("dict[str, object]", message_value)
        message_info_value = message.get("info")
        if not isinstance(message_info_value, dict):
            warnings.append(_issue("invalid_message", message_location, "消息结构无效，已跳过。"))
            continue
        message_info = cast("dict[str, object]", message_info_value)
        if message_info.get("role") != "assistant":
            continue

        message_id = message_info.get("id")
        message_session_id = message_info.get("sessionID")
        parts_value = message.get("parts")
        if (
            not _is_safe_external_id(message_id)
            or message_session_id != session_id
            or not isinstance(parts_value, list)
        ):
            warnings.append(_issue("invalid_message", message_location, "消息结构无效，已跳过。"))
            continue
        parts = cast("list[object]", parts_value)
        persisted_error = message_info.get("error") is not None

        for part_index, part_value in enumerate(parts):
            part_location = f"{message_location}.parts[{part_index}]"
            if not isinstance(part_value, dict):
                warnings.append(
                    _issue("invalid_tool_part", part_location, "工具记录结构无效，已跳过。")
                )
                continue
            part = cast("dict[str, object]", part_value)
            part_type = part.get("type")
            if part_type != "tool":
                if isinstance(part_type, str) and part_type in _KNOWN_NON_TOOL_PARTS:
                    continue
                warnings.append(
                    _issue("unknown_part_type", part_location, "未知记录类型，已跳过。")
                )
                continue

            part_id = part.get("id")
            part_session_id = part.get("sessionID")
            part_message_id = part.get("messageID")
            tool = part.get("tool")
            state_value = part.get("state")
            if (
                not _is_safe_external_id(part_id)
                or part_session_id != session_id
                or part_message_id != message_id
                or not isinstance(tool, str)
                or not tool
                or not isinstance(state_value, dict)
            ):
                warnings.append(
                    _issue("invalid_tool_part", part_location, "工具记录结构无效，已跳过。")
                )
                continue
            state = cast("dict[str, object]", state_value)
            status = state.get("status")
            input_value = state.get("input")
            if (
                not isinstance(status, str)
                or status not in _SUPPORTED_STATUS
                or not isinstance(input_value, dict)
            ):
                warnings.append(
                    _issue("invalid_tool_part", part_location, "工具记录结构无效，已跳过。")
                )
                continue
            input_data = cast("dict[str, object]", input_value)

            identity = (session_id, message_id, part_id)
            if identity in seen_parts:
                warnings.append(
                    _issue("duplicate_tool_part", part_location, "重复工具记录已跳过。")
                )
                continue
            seen_parts.add(identity)

            if status in _INCOMPLETE_STATUS and not persisted_error:
                warnings.append(
                    _issue(
                        "incomplete_tool_part",
                        part_location,
                        "工具记录仍未完成，已跳过。",
                    )
                )
                continue

            source_ref = _source_ref(session_id, message_id, part_id)
            event = ObservableEvent(
                id=f"evt-trace-{hashlib.sha256(source_ref.encode()).hexdigest()[:16]}",
                kind=EventKind.TRACE_RECORD,
                summary=_summary(
                    tool=tool,
                    status=status,
                    input_data=input_data,
                    persisted_message_error=persisted_error,
                    project_root=project_root,
                ),
                source_refs=(
                    SourceRef(
                        type=SourceType.TRACE_RECORD,
                        ref=source_ref,
                        note="opencode",
                    ),
                    SourceRef(
                        type=SourceType.FILE,
                        ref=export_path.resolve().as_posix(),
                        note="session-export",
                    ),
                ),
                occurred_at=_occurred_at(
                    state,
                    location=part_location,
                    warnings=warnings,
                ),
            )
            event_validator.validate("observable_event", event.to_dict())
            events.append(event)

    events.sort(key=_event_sort_key)
    warnings.sort(key=lambda issue: (issue.location, issue.code, issue.message))
    result_status = TraceInputStatus.PARSED if events else TraceInputStatus.AUTHORIZED_NOT_FOUND
    return TraceAdapterResult(
        status=result_status,
        events=tuple(events),
        warnings=tuple(warnings),
    )


def adapt_opencode_exports(
    export_paths: tuple[Path, ...],
    *,
    authorized_paths: tuple[Path, ...],
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    """Merge multiple explicitly authorized OpenCode session exports.

    Authorization is matched per path before a file is opened. Repeating an
    overlapping export is safe: identical events are retained once while their
    session-bearing ``trace://`` provenance remains on the event.
    """

    if not export_paths:
        return TraceAdapterResult(status=TraceInputStatus.NOT_PROVIDED)

    def path_key(path: Path) -> str:
        try:
            return os.path.normcase(str(path.resolve(strict=False)))
        except OSError:
            return os.path.normcase(str(path.absolute()))

    authorized_keys = {path_key(path) for path in authorized_paths}
    if not authorized_keys:
        return TraceAdapterResult(status=TraceInputStatus.NOT_AUTHORIZED)

    events_by_id: dict[str, ObservableEvent] = {}
    warnings: list[TraceParseIssue] = []
    any_authorized = False
    for export_index, export_path in enumerate(export_paths):
        prefix = f"exports[{export_index}]"
        if path_key(export_path) not in authorized_keys:
            warnings.append(
                _issue(
                    "export_not_authorized",
                    prefix,
                    "该 OpenCode 会话导出未获单独授权，未读取。",
                )
            )
            continue
        any_authorized = True
        result = adapt_opencode_export(
            export_path,
            authorized=True,
            project_root=project_root,
            validator=validator,
        )
        warnings.extend(
            _issue(issue.code, f"{prefix}.{issue.location}", issue.message)
            for issue in result.warnings
        )
        duplicate_count = 0
        for event in result.events:
            existing = events_by_id.get(event.id)
            if existing is None:
                events_by_id[event.id] = event
                continue
            if events_conflict(existing, event):
                raise UnsupportedOpenCodeFormatError(
                    "多个 OpenCode 导出包含 ID 相同但内容冲突的工具记录。"
                )
            duplicate_count += 1
        if duplicate_count:
            warnings.append(
                _issue(
                    "duplicate_export_event",
                    prefix,
                    f"与先前会话导出重叠的 {duplicate_count} 条工具记录已去重。",
                )
            )

    if not any_authorized:
        return TraceAdapterResult(
            status=TraceInputStatus.NOT_AUTHORIZED,
            warnings=tuple(warnings),
        )
    events = tuple(sorted(events_by_id.values(), key=_event_sort_key))
    warnings.sort(key=lambda issue: (issue.location, issue.code, issue.message))
    return TraceAdapterResult(
        status=(TraceInputStatus.PARSED if events else TraceInputStatus.AUTHORIZED_NOT_FOUND),
        events=events,
        warnings=tuple(warnings),
    )
