"""已有 pytest 风格测试日志的只读解析。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers._common import (
    MAX_TEXT_FILE_BYTES,
    compact_text,
    repo_root,
    resolve_project_file,
    stable_event_id,
)
from learntrace.parsers.types import ParseResult, ParseWarning

_COUNT_LABELS = (
    ("passed", "通过"),
    ("failed", "失败"),
    ("errors?", "错误"),
    ("skipped", "跳过"),
    ("xfailed", "预期失败"),
    ("xpassed", "意外通过"),
)
_COUNT_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<label>passed|failed|errors?|skipped|xfailed|xpassed)\b",
    re.IGNORECASE,
)
_FAILED_RE = re.compile(r"^FAILED\s+(?P<node>\S+)", re.IGNORECASE)


def _summary_counts(lines: list[str]) -> tuple[int, dict[str, int]] | None:
    for number in range(len(lines), 0, -1):
        matches = list(_COUNT_RE.finditer(lines[number - 1]))
        if matches:
            return number, {
                match.group("label").lower().rstrip("s"): int(match.group("count"))
                for match in matches
            }
    return None


def _count_summary(counts: dict[str, int]) -> str:
    labels = {pattern.rstrip("?").rstrip("s"): chinese for pattern, chinese in _COUNT_LABELS}
    ordered: list[str] = []
    for key in ("passed", "failed", "error", "skipped", "xfailed", "xpassed"):
        if key in counts:
            ordered.append(f"{counts[key]} 个{labels[key]}")
    return "，".join(ordered)


def _parse_test_log(root: Path, requested_path: Path) -> ParseResult:
    try:
        path, relative = resolve_project_file(root, requested_path)
    except ValueError as error:
        return ParseResult(
            warnings=(ParseWarning("invalid_source", requested_path.as_posix(), str(error)),)
        )
    try:
        if path.stat().st_size > MAX_TEXT_FILE_BYTES:
            return ParseResult(
                warnings=(
                    ParseWarning(
                        "file_too_large",
                        relative,
                        f"file exceeds {MAX_TEXT_FILE_BYTES} bytes",
                    ),
                )
            )
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ParseResult(
            warnings=(ParseWarning("invalid_utf8", relative, "file is not valid UTF-8"),)
        )
    except OSError as error:
        return ParseResult(warnings=(ParseWarning("read_error", relative, str(error)),))

    lines = text.splitlines()
    events: list[ObservableEvent] = []
    counts_result = _summary_counts(lines)
    if counts_result is not None:
        number, counts = counts_result
        content = _count_summary(counts)
        location = f"{relative}:{number}"
        events.append(
            ObservableEvent(
                id=stable_event_id("test", relative, str(number), content),
                kind=EventKind.TEST_LOG,
                summary=f"已有测试日志记录：{content}。",
                source_refs=(SourceRef(type=SourceType.TEST_LOG, ref=location),),
            )
        )

    for number, line in enumerate(lines, start=1):
        match = _FAILED_RE.match(line.strip())
        if match is None:
            continue
        node = compact_text(match.group("node"), limit=200)
        source_refs = [SourceRef(type=SourceType.TEST_LOG, ref=f"{relative}:{number}")]
        test_path = node.split("::", maxsplit=1)[0]
        if test_path and not Path(test_path).is_absolute():
            source_refs.append(SourceRef(type=SourceType.FILE, ref=Path(test_path).as_posix()))
        events.append(
            ObservableEvent(
                id=stable_event_id("test", relative, str(number), node),
                kind=EventKind.TEST_LOG,
                summary=f"测试日志记录用例 {node} 失败。",
                source_refs=tuple(source_refs),
            )
        )

    if events:
        return ParseResult(events=tuple(events))
    return ParseResult(
        warnings=(
            ParseWarning(
                "unrecognized_test_log",
                relative,
                "no supported pytest result summary was found",
            ),
        )
    )


def parse_test_logs(project_root: Path, paths: Iterable[Path]) -> ParseResult:
    """解析用户已确认的已有测试日志，不执行任何测试命令。"""
    root = repo_root(project_root)
    result = ParseResult()
    for path in paths:
        result = result.merged(_parse_test_log(root, path))
    return result
