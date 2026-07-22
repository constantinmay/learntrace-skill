"""已有 pytest 风格测试日志的只读解析。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers._common import (
    MAX_TEXT_FILE_BYTES,
    compact_text,
    project_reference,
    repo_root,
    resolve_project_file,
    safe_os_error,
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
_ERROR_RE = re.compile(r"^ERROR\s+(?P<node>\S+)", re.IGNORECASE)
_DURATION_RE = re.compile(r"\bin\s+(?P<duration>\d+(?:\.\d+)?)s\b", re.IGNORECASE)
_GENERIC_SUMMARY_RE = re.compile(
    r"Tests\s+run:\s*(?P<total>\d+)\s*,\s*Failures:\s*(?P<failed>\d+)\s*,\s*"
    r"Errors:\s*(?P<error>\d+)\s*,\s*Skipped:\s*(?P<skipped>\d+)",
    re.IGNORECASE,
)


def _summary_counts(lines: list[str]) -> list[tuple[int, dict[str, int], str | None]]:
    summaries: list[tuple[int, dict[str, int], str | None]] = []
    for number, line in enumerate(lines, start=1):
        matches = list(_COUNT_RE.finditer(line))
        counts: dict[str, int] = {}
        if matches:
            counts = {
                match.group("label").lower().rstrip("s"): int(match.group("count"))
                for match in matches
            }
        else:
            generic = _GENERIC_SUMMARY_RE.search(line)
            if generic is not None:
                counts = {
                    "total": int(generic.group("total")),
                    "failed": int(generic.group("failed")),
                    "error": int(generic.group("error")),
                    "skipped": int(generic.group("skipped")),
                }
        if counts:
            duration_match = _DURATION_RE.search(line)
            duration = duration_match.group("duration") if duration_match is not None else None
            summaries.append((number, counts, duration))
    return summaries


def _count_summary(counts: dict[str, int]) -> str:
    labels = {pattern.rstrip("?").rstrip("s"): chinese for pattern, chinese in _COUNT_LABELS}
    ordered: list[str] = []
    if "total" in counts:
        ordered.append(f"共运行 {counts['total']} 个")
    for key in ("passed", "failed", "error", "skipped", "xfailed", "xpassed"):
        if key in counts:
            ordered.append(f"{counts[key]} 个{labels[key]}")
    return "，".join(ordered)


def _parse_test_log(root: Path, requested_path: Path) -> ParseResult:
    try:
        path, relative = resolve_project_file(root, requested_path)
    except ValueError as error:
        return ParseResult(
            warnings=(
                ParseWarning("invalid_source", project_reference(root, requested_path), str(error)),
            )
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
        return ParseResult(warnings=(ParseWarning("read_error", relative, safe_os_error(error)),))

    lines = text.splitlines()
    events: list[ObservableEvent] = []
    for number, counts, duration in _summary_counts(lines):
        content = _count_summary(counts)
        duration_text = f"，耗时 {duration} 秒" if duration is not None else ""
        location = f"{relative}:{number}"
        events.append(
            ObservableEvent(
                id=stable_event_id("test", relative, str(number), content, duration or ""),
                kind=EventKind.TEST_LOG,
                summary=f"已有测试日志记录：{content}{duration_text}。",
                source_refs=(SourceRef(type=SourceType.TEST_LOG, ref=location),),
            )
        )

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        match = _FAILED_RE.match(stripped)
        outcome = "失败"
        if match is None:
            match = _ERROR_RE.match(stripped)
            outcome = "错误"
        if match is None:
            continue
        raw_node = match.group("node")
        node = compact_text(raw_node)
        source_refs = [SourceRef(type=SourceType.TEST_LOG, ref=f"{relative}:{number}")]
        test_path = node.split("::", maxsplit=1)[0]
        if test_path and not Path(test_path).is_absolute():
            source_refs.append(SourceRef(type=SourceType.FILE, ref=Path(test_path).as_posix()))
        events.append(
            ObservableEvent(
                id=stable_event_id("test", relative, str(number), outcome, raw_node),
                kind=EventKind.TEST_LOG,
                summary=f"测试日志记录用例 {node} 出现{outcome}。",
                source_refs=tuple(source_refs),
            )
        )

    if events:
        return ParseResult(events=tuple(events))
    nonempty_lines = [number for number, line in enumerate(lines, start=1) if line.strip()]
    if not nonempty_lines:
        return ParseResult(
            warnings=(ParseWarning("empty_test_log", relative, "test log is empty"),)
        )
    start, end = nonempty_lines[0], nonempty_lines[-1]
    fallback = ObservableEvent(
        id=stable_event_id("test", relative, str(start), str(end), text),
        kind=EventKind.TEST_LOG,
        summary=f"已有日志文件 {relative}，共 {len(lines)} 行；格式未识别，未提取测试结果。",
        source_refs=(SourceRef(type=SourceType.TEST_LOG, ref=f"{relative}:{start}-{end}"),),
    )
    return ParseResult(
        events=(fallback,),
        warnings=(
            ParseWarning(
                "unsupported_test_log_format",
                relative,
                "test result format is unsupported",
            ),
        ),
    )


def parse_test_logs(project_root: Path, paths: Iterable[Path]) -> ParseResult:
    """解析用户已确认的已有测试日志，不执行任何测试命令。"""
    root = repo_root(project_root)
    result = ParseResult()
    for path in paths:
        result = result.merged(_parse_test_log(root, path))
    return result
