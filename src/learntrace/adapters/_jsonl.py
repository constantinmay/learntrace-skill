"""Shared streaming JSONL reader for Claude Code and Codex session files."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

from learntrace.adapters.types import TraceParseIssue, UnsupportedTraceFormatError

_MAX_SESSION_BYTES = 256 * 1024 * 1024
_MAX_LINE_BYTES = 16 * 1024 * 1024


def _issue(code: str, location: str, message: str) -> TraceParseIssue:
    return TraceParseIssue(code=code, location=location, message=message)


def _decode_line(
    line_number: int,
    raw_line: bytes,
    *,
    byte_markers: tuple[bytes, ...],
    warnings: list[TraceParseIssue],
) -> Iterator[tuple[int, dict[str, object]]]:
    location = f"lines[{line_number - 1}]"
    stripped = raw_line.rstrip(b"\r\n")
    if not stripped:
        return
    if not any(marker in stripped for marker in byte_markers):
        return
    if len(stripped) > _MAX_LINE_BYTES:
        warnings.append(_issue("line_too_large", location, "单行超过 16 MiB 限制，已跳过。"))
        return
    try:
        text = stripped.decode("utf-8")
    except UnicodeDecodeError:
        warnings.append(_issue("invalid_line", location, "行不是有效 UTF-8，已跳过。"))
        return
    try:
        parsed: object = json.loads(text)
    except (ValueError, RecursionError):
        warnings.append(_issue("invalid_line", location, "行不是有效 JSON，已跳过。"))
        return
    if not isinstance(parsed, dict):
        warnings.append(_issue("invalid_line", location, "行不是 JSON 对象，已跳过。"))
        return
    yield line_number, cast("dict[str, object]", parsed)


def _decode_final_line(
    line_number: int,
    raw_line: bytes,
    *,
    byte_markers: tuple[bytes, ...],
    warnings: list[TraceParseIssue],
) -> Iterator[tuple[int, dict[str, object]]]:
    location = f"lines[{line_number - 1}]"
    try:
        text = raw_line.rstrip(b"\r\n").decode("utf-8")
        parsed: object = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("not an object")
        warnings.append(
            _issue(
                "truncated_final_line",
                location,
                "末尾行缺少换行符，已按完整记录解析。",
            )
        )
        yield line_number, cast("dict[str, object]", parsed)
    except (UnicodeDecodeError, ValueError, RecursionError):
        warnings.append(_issue("truncated_final_line", location, "末尾行不完整，已跳过。"))


def check_session_size(path: Path, *, label: str) -> None:
    """Reject oversized session files before streaming them."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UnsupportedTraceFormatError(f"无法读取 {label} 会话文件。") from exc
    if size > _MAX_SESSION_BYTES:
        raise UnsupportedTraceFormatError(f"{label} 会话文件超过 256 MiB 限制。")


def iter_jsonl_records(
    path: Path,
    *,
    label: str,
    markers: tuple[str, ...],
    warnings: list[TraceParseIssue],
) -> Iterator[tuple[int, dict[str, object]]]:
    """Stream ``(line_number, record)`` pairs from one JSONL session file.

    Lines that contain none of ``markers`` are skipped before JSON parsing so
    large tool-result payloads never reach the parser. Malformed lines become
    source-safe warnings instead of aborting the stream, and a final line
    without a trailing newline is tolerated because the session file may still
    be appended to by its host.
    """

    check_session_size(path, label=label)
    byte_markers = tuple(marker.encode("utf-8") for marker in markers)
    try:
        handle = path.open("rb")
    except OSError as exc:
        raise UnsupportedTraceFormatError(f"无法读取 {label} 会话文件。") from exc
    with handle:
        pending: tuple[int, bytes] | None = None
        for line_number, raw_line in enumerate(handle, start=1):
            if pending is not None:
                yield from _decode_line(
                    pending[0],
                    pending[1],
                    byte_markers=byte_markers,
                    warnings=warnings,
                )
            pending = (line_number, raw_line)
        if pending is not None and not pending[1].endswith(b"\n"):
            yield from _decode_final_line(
                pending[0],
                pending[1],
                byte_markers=byte_markers,
                warnings=warnings,
            )
        elif pending is not None:
            yield from _decode_line(
                pending[0],
                pending[1],
                byte_markers=byte_markers,
                warnings=warnings,
            )


__all__ = ["_MAX_LINE_BYTES", "_MAX_SESSION_BYTES", "check_session_size", "iter_jsonl_records"]
