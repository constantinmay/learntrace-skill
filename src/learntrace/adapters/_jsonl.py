"""Shared streaming JSONL reader for Claude Code and Codex session files."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import cast

from learntrace.adapters.types import TraceParseIssue, UnsupportedTraceFormatError

_MAX_SESSION_BYTES = 256 * 1024 * 1024
_MAX_LINE_BYTES = 16 * 1024 * 1024
_MAX_WARNINGS_PER_SESSION = 200
_SUMMARY_WARNING_CODES = frozenset(
    {"warning_cap_reached", "event_cap_reached", "tracked_call_cap_reached"}
)


class WarningLog:
    """Collect parse warnings with a hard cap to bound parse-phase memory.

    Detailed issues are kept until the cap; beyond it only a count is kept and
    a single summary warning is appended by :meth:`finalize`. Summary warnings
    (cap-reached rollups) live on a separate channel so they always survive
    regardless of how many detailed issues were dropped; :meth:`add` and
    :meth:`extend` route known summary codes there automatically, which keeps
    summaries intact when a caller re-logs another adapter's result.
    """

    def __init__(self) -> None:
        self._issues: list[TraceParseIssue] = []
        self._summaries: list[TraceParseIssue] = []
        self._dropped = 0

    def add(self, code: str, location: str, message: str) -> None:
        if code in _SUMMARY_WARNING_CODES:
            self.add_summary(code, location, message)
            return
        if len(self._issues) < _MAX_WARNINGS_PER_SESSION:
            self._issues.append(TraceParseIssue(code=code, location=location, message=message))
        else:
            self._dropped += 1

    def add_summary(self, code: str, location: str, message: str) -> None:
        """Record a cap-reached summary that never counts against the detail cap."""
        self._summaries.append(TraceParseIssue(code=code, location=location, message=message))

    def extend(self, issues: Iterable[TraceParseIssue]) -> None:
        for issue in issues:
            self.add(issue.code, issue.location, issue.message)

    def finalize(self) -> list[TraceParseIssue]:
        """Return detailed issues followed by summaries; call exactly once."""
        if self._dropped:
            self._summaries.append(
                TraceParseIssue(
                    code="warning_cap_reached",
                    location="warnings",
                    message=(
                        f"另有 {self._dropped} 条警告超过 {_MAX_WARNINGS_PER_SESSION} 条上限，"
                        "未逐一记录。"
                    ),
                )
            )
        return [*self._issues, *self._summaries]


def _decode_line(
    line_number: int,
    raw_line: bytes,
    *,
    byte_markers: tuple[bytes, ...],
    warnings: WarningLog,
    final: bool = False,
) -> Iterator[tuple[int, dict[str, object]]]:
    """Decode one JSONL line, applying the full check chain to final lines too.

    Marker pre-filtering and the per-line size limit run before JSON parsing
    regardless of whether the line is newline-terminated, so a file that is
    still being appended cannot smuggle oversized or marker-free payloads past
    the parser through its last line.
    """

    location = f"lines[{line_number - 1}]"
    stripped = raw_line.rstrip(b"\r\n")
    if not stripped:
        return
    if not any(marker in stripped for marker in byte_markers):
        return
    if len(stripped) > _MAX_LINE_BYTES:
        warnings.add("line_too_large", location, "单行超过 16 MiB 限制，已跳过。")
        return
    try:
        text = stripped.decode("utf-8")
    except UnicodeDecodeError:
        warnings.add("invalid_line", location, "行不是有效 UTF-8，已跳过。")
        return
    try:
        parsed: object = json.loads(text)
    except (ValueError, RecursionError):
        if final:
            warnings.add("truncated_final_line", location, "末尾行不完整，已跳过。")
        else:
            warnings.add("invalid_line", location, "行不是有效 JSON，已跳过。")
        return
    if not isinstance(parsed, dict):
        if final:
            warnings.add("truncated_final_line", location, "末尾行不完整，已跳过。")
        else:
            warnings.add("invalid_line", location, "行不是 JSON 对象，已跳过。")
        return
    if final:
        warnings.add(
            "truncated_final_line",
            location,
            "末尾行缺少换行符，已按完整记录解析。",
        )
    yield line_number, cast("dict[str, object]", parsed)


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
    warnings: WarningLog,
) -> Iterator[tuple[int, dict[str, object]]]:
    """Stream ``(line_number, record)`` pairs from one JSONL session file.

    Lines that contain none of ``markers`` are skipped before JSON parsing so
    large tool-result payloads never reach the parser. Malformed lines become
    source-safe warnings instead of aborting the stream, and a final line
    without a trailing newline is tolerated because the session file may still
    be appended to by its host — it passes through the same size and marker
    checks as any other line before being accepted.
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
        if pending is not None:
            yield from _decode_line(
                pending[0],
                pending[1],
                byte_markers=byte_markers,
                warnings=warnings,
                final=not pending[1].endswith(b"\n"),
            )


__all__ = [
    "_MAX_LINE_BYTES",
    "_MAX_SESSION_BYTES",
    "_MAX_WARNINGS_PER_SESSION",
    "WarningLog",
    "check_session_size",
    "iter_jsonl_records",
]
