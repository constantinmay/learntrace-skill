"""已有 pytest 与 Maven/JUnit 测试日志的只读解析。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath

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
_PYTEST_COUNT_SEGMENT = (
    r"\d+\s+(?:passed|failed|errors?|skipped|xfailed|xpassed|warnings?|deselected)"
)
_PYTEST_SUMMARY_RE = re.compile(
    rf"^{_PYTEST_COUNT_SEGMENT}(?:\s*,\s*{_PYTEST_COUNT_SEGMENT})*"
    r"(?:\s+in\s+\d+(?:\.\d+)?s)?$",
    re.IGNORECASE,
)
_PYTEST_MARKER_RE = re.compile(
    r"^(?:=+\s*(?:test session starts|short test summary info)\s*=+|"
    r"platform\b.*\bpytest-\d)",
    re.IGNORECASE,
)
_PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(?P<node>\S+)", re.IGNORECASE)
_PYTEST_ERROR_RE = re.compile(r"^ERROR\s+(?P<node>\S+)", re.IGNORECASE)
_MAVEN_CASE_RE = re.compile(
    r"^\[ERROR\]\s+(?P<node>.+?)\s+(?:--\s+)?Time elapsed:.*?"
    r"<<<\s+(?P<outcome>FAILURE|ERROR)!\s*$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"\bin\s+(?P<duration>\d+(?:\.\d+)?)s\b", re.IGNORECASE)
_GENERIC_SUMMARY_RE = re.compile(
    r"Tests\s+run:\s*(?P<total>\d+)\s*,\s*Failures:\s*(?P<failed>\d+)\s*,\s*"
    r"Errors:\s*(?P<error>\d+)\s*,\s*Skipped:\s*(?P<skipped>\d+)",
    re.IGNORECASE,
)


def _pytest_summary_body(line: str) -> str | None:
    stripped = line.strip()
    has_delimiters = stripped.startswith("=") and stripped.endswith("=")
    body = stripped.strip("= ")
    if _PYTEST_SUMMARY_RE.fullmatch(body) is None:
        return None
    if not has_delimiters and _DURATION_RE.search(body) is None:
        return None
    return body


def _summary_counts(lines: list[str]) -> list[tuple[int, dict[str, int], str | None]]:
    summaries: list[tuple[int, dict[str, int], str | None]] = []
    for number, line in enumerate(lines, start=1):
        pytest_body = _pytest_summary_body(line)
        matches = list(_COUNT_RE.finditer(pytest_body)) if pytest_body is not None else []
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
            duration_match = _DURATION_RE.search(pytest_body or line)
            duration = duration_match.group("duration") if duration_match is not None else None
            summaries.append((number, counts, duration))
    return summaries


def _has_pytest_context(lines: list[str]) -> bool:
    return any(
        _pytest_summary_body(line) is not None or _PYTEST_MARKER_RE.match(line.strip()) is not None
        for line in lines
    )


def _has_junit_context(lines: list[str]) -> bool:
    return any(_GENERIC_SUMMARY_RE.search(line) is not None for line in lines)


def _pytest_case(line: str) -> tuple[str, str] | None:
    match = _PYTEST_FAILED_RE.match(line)
    outcome = "失败"
    if match is None:
        match = _PYTEST_ERROR_RE.match(line)
        outcome = "错误"
    if match is None:
        return None

    node = match.group("node")
    test_path = node.split("::", maxsplit=1)[0]
    if "::" not in node and Path(test_path).suffix.lower() != ".py":
        return None
    return node, outcome


def _maven_case(line: str) -> tuple[str, str] | None:
    match = _MAVEN_CASE_RE.match(line)
    if match is None:
        return None
    outcome = "失败" if match.group("outcome").upper() == "FAILURE" else "错误"
    return match.group("node"), outcome


def _case_file_reference(root: Path, raw_path: str) -> str | None:
    """Return a normalized project-relative path for a pytest node, if safe."""
    if not raw_path or "\x00" in raw_path:
        return None
    windows_path = PureWindowsPath(raw_path)
    normalized = raw_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if windows_path.drive or windows_path.root or posix_path.is_absolute():
        return None
    if any(part == ".." for part in posix_path.parts):
        return None
    safe_parts = tuple(part for part in posix_path.parts if part not in {"", "."})
    if not safe_parts:
        return None
    try:
        candidate = (root / Path(*safe_parts)).resolve()
    except (OSError, RuntimeError):
        return None
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    return relative.as_posix()


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

    allow_pytest_cases = _has_pytest_context(lines) or _has_junit_context(lines)
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        case = _pytest_case(stripped) if allow_pytest_cases else None
        pytest_case = case is not None
        if case is None:
            case = _maven_case(stripped)
        if case is None:
            continue
        raw_node, outcome = case
        node = compact_text(raw_node)
        source_refs = [SourceRef(type=SourceType.TEST_LOG, ref=f"{relative}:{number}")]
        test_path = raw_node.split("::", maxsplit=1)[0]
        file_reference = _case_file_reference(root, test_path) if pytest_case else None
        if file_reference is not None:
            source_refs.append(SourceRef(type=SourceType.FILE, ref=file_reference))
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
