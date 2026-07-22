"""UTF-8 Markdown 与纯文本文档的确定性只读解析。"""

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

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _markdown_sections(lines: list[str], fallback_title: str) -> list[tuple[int, int, str, str]]:
    sections: list[tuple[int, int, str, str]] = []
    title = fallback_title
    start = 1
    body: list[str] = []

    def append_section(end: int) -> None:
        text = compact_text("\n".join(body))
        if text:
            sections.append((start, end, title, text))

    for number, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match is None:
            body.append(line)
            continue
        append_section(number - 1)
        title = compact_text(match.group(2), limit=120)
        start = number
        body = []
    append_section(len(lines))
    return sections


def _text_paragraphs(lines: list[str]) -> list[tuple[int, int, str]]:
    paragraphs: list[tuple[int, int, str]] = []
    start = 1
    body: list[str] = []

    def append_paragraph(end: int) -> None:
        text = compact_text("\n".join(body))
        if text:
            paragraphs.append((start, end, text))

    for number, line in enumerate(lines, start=1):
        if line.strip():
            if not body:
                start = number
            body.append(line)
            continue
        append_paragraph(number - 1)
        body = []
    append_paragraph(len(lines))
    return paragraphs


def _parse_document(root: Path, requested_path: Path) -> ParseResult:
    try:
        path, relative = resolve_project_file(root, requested_path)
    except ValueError as error:
        return ParseResult(
            warnings=(ParseWarning("invalid_source", requested_path.as_posix(), str(error)),)
        )

    if path.suffix.lower() not in {".md", ".txt"}:
        return ParseResult(
            warnings=(
                ParseWarning(
                    "unsupported_document",
                    relative,
                    "only UTF-8 Markdown and plain-text documents are supported",
                ),
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
        return ParseResult(warnings=(ParseWarning("read_error", relative, str(error)),))

    lines = text.splitlines()
    events: list[ObservableEvent] = []
    if path.suffix.lower() == ".md":
        for start, end, title, content in _markdown_sections(lines, path.stem):
            summary = f"文档章节“{title}”记录：{content}"
            location = f"{relative}:{start}-{max(start, end)}"
            events.append(
                ObservableEvent(
                    id=stable_event_id("doc", relative, str(start), str(end), content),
                    kind=EventKind.DOCUMENT,
                    summary=summary,
                    source_refs=(SourceRef(type=SourceType.DOCUMENT, ref=location),),
                )
            )
    else:
        for start, end, content in _text_paragraphs(lines):
            location = f"{relative}:{start}-{max(start, end)}"
            events.append(
                ObservableEvent(
                    id=stable_event_id("doc", relative, str(start), str(end), content),
                    kind=EventKind.DOCUMENT,
                    summary=f"文本文档记录：{content}",
                    source_refs=(SourceRef(type=SourceType.DOCUMENT, ref=location),),
                )
            )
    if events:
        return ParseResult(events=tuple(events))
    return ParseResult(
        warnings=(ParseWarning("empty_document", relative, "document contains no recordable text"),)
    )


def parse_documents(project_root: Path, paths: Iterable[Path]) -> ParseResult:
    """解析用户已确认的 Markdown/TXT 路径。"""
    root = repo_root(project_root)
    result = ParseResult()
    for path in paths:
        result = result.merged(_parse_document(root, path))
    return result
