"""UTF-8 Markdown 与纯文本文档的确定性只读解析。"""

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

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})")
_CHUNK_TARGET_CHARS = 600


def _text_blocks(lines: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """按段落分块；围栏代码块在正常大小下保持完整。"""
    blocks: list[tuple[int, int, str]] = []
    body: list[str] = []
    start = 1
    end = 1
    code_fence: tuple[str, int] | None = None

    def append_block() -> None:
        text = compact_text("\n".join(body))
        if text:
            blocks.append((start, end, text))

    for number, line in lines:
        stripped = line.strip()
        fence_match = _FENCE_RE.match(stripped)
        if fence_match is not None:
            fence = fence_match.group("fence")
            if not body:
                start = number
            body.append(line)
            end = number
            if code_fence is None:
                code_fence = (fence[0], len(fence))
            elif fence[0] == code_fence[0] and len(fence) >= code_fence[1]:
                code_fence = None
            continue
        if not stripped and code_fence is None:
            append_block()
            body = []
            continue
        if not body:
            start = number
        body.append(line)
        end = number
    append_block()
    return blocks


def _pack_blocks(blocks: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    chunks: list[tuple[int, int, str]] = []
    current: list[str] = []
    start = 1
    end = 1

    def append_chunk() -> None:
        if current:
            chunks.append((start, end, " ".join(current)))

    for block_start, block_end, text in blocks:
        projected = sum(len(item) for item in current) + len(current) + len(text)
        if current and projected > _CHUNK_TARGET_CHARS:
            append_chunk()
            current = []
        if not current:
            start = block_start
        current.append(text)
        end = block_end
    append_chunk()
    return chunks


def _markdown_sections(
    lines: list[str], fallback_title: str
) -> list[tuple[int, str, list[tuple[int, str]]]]:
    sections: list[tuple[int, str, list[tuple[int, str]]]] = []
    title = fallback_title
    heading_line = 1
    body: list[tuple[int, str]] = []

    def append_section() -> None:
        if _text_blocks(body):
            sections.append((heading_line, title, list(body)))

    for number, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line)
        if match is None:
            body.append((number, line))
            continue
        append_section()
        title = compact_text(match.group(2))
        heading_line = number
        body = []
    append_section()
    return sections


def _text_paragraphs(lines: list[str]) -> list[tuple[int, int, str]]:
    numbered = list(enumerate(lines, start=1))
    return _text_blocks(numbered)


def _parse_document(root: Path, requested_path: Path) -> ParseResult:
    try:
        path, relative = resolve_project_file(root, requested_path)
    except ValueError as error:
        return ParseResult(
            warnings=(
                ParseWarning("invalid_source", project_reference(root, requested_path), str(error)),
            )
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
        return ParseResult(warnings=(ParseWarning("read_error", relative, safe_os_error(error)),))

    lines = text.splitlines()
    events: list[ObservableEvent] = []
    if path.suffix.lower() == ".md":
        for heading_line, title, section_lines in _markdown_sections(lines, path.stem):
            for chunk_index, (start, end, content) in enumerate(
                _pack_blocks(_text_blocks(section_lines)), start=1
            ):
                source_start = min(heading_line, start) if chunk_index == 1 else start
                summary = f"文档章节“{title}”记录：{content}"
                location = f"{relative}:{source_start}-{max(source_start, end)}"
                events.append(
                    ObservableEvent(
                        id=stable_event_id("doc", relative, str(source_start), str(end), content),
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
