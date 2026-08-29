"""Paged source readback for historical revisions and the current worktree."""

from __future__ import annotations

import base64
import hashlib
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from learntrace.artifacts import EvidencePathPolicy, register_generated_artifacts
from learntrace.parsers._common import safe_os_error
from learntrace.parsers.git import (
    git_command,
    git_timeout_seconds,
    path_is_within,
    run_git,
    safe_repository_path,
    validated_git_root,
)
from learntrace.parsers.git_navigation import (
    DEFAULT_SOURCE_LINES,
    GitNavigationResult,
    local_navigation_output,
    resolve_revision,
    stable_navigation_id,
    write_navigation_json,
)

_LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID_RE = re.compile(rb"^oid sha256:([0-9a-f]{64})$", re.MULTILINE)
_LFS_SIZE_RE = re.compile(rb"^size ([0-9]+)$", re.MULTILINE)
_PROBE_BYTES = 8192
_READ_CHUNK = 64 * 1024
_MAX_RESPONSE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class _Source:
    root: Path
    revision: str
    repository_path: str
    object_id: str
    size_bytes: int
    worktree_path: Path | None


@dataclass(frozen=True, slots=True)
class _Page:
    content: bytes
    start_byte: int
    end_byte: int
    end_line: int | None
    reached_eof: bool
    total_lines: int | None
    continuation: dict[str, int] | None
    interrupted_reason: str | None = None


def _hash_worktree(root: Path, repository_path: str) -> str | None:
    result = run_git(root, "hash-object", "--no-filters", "--", repository_path)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _index_object_id(root: Path, repository_path: str) -> str | None:
    result = run_git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f":{repository_path}",
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _worktree_source(root: Path, repository_path: str) -> tuple[_Source | None, str | None]:
    candidate = root / Path(repository_path)
    try:
        if candidate.is_symlink():
            return None, "symlink_not_followed"
        resolved = candidate.resolve(strict=True)
        if not path_is_within(resolved, root):
            return None, "path_outside_project"
        if not resolved.is_file():
            return None, "not_a_file"
        size = resolved.stat().st_size
    except FileNotFoundError:
        return None, "not_present"
    except OSError as error:
        return None, safe_os_error(error)
    object_id = _hash_worktree(root, repository_path)
    if object_id is None:
        return None, "worktree_object_id_unavailable"
    return _Source(root, "worktree", repository_path, object_id, size, resolved), None


def _revision_source(
    root: Path,
    commit_id: str,
    repository_path: str,
) -> tuple[_Source | None, str | None]:
    object_result = run_git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{commit_id}:{repository_path}",
    )
    if object_result.returncode != 0 or not object_result.stdout.strip():
        return None, "not_present"
    object_id = object_result.stdout.strip()
    type_result = run_git(root, "cat-file", "-t", object_id)
    if type_result.returncode != 0 or type_result.stdout.strip() != "blob":
        return None, "not_a_blob"
    size_result = run_git(root, "cat-file", "-s", object_id)
    if size_result.returncode != 0 or not size_result.stdout.strip().isdigit():
        return None, "blob_size_unavailable"
    return _Source(root, commit_id, repository_path, object_id, int(size_result.stdout), None), None


def _index_source(root: Path, repository_path: str) -> tuple[_Source | None, str | None]:
    object_id = _index_object_id(root, repository_path)
    if object_id is None:
        return None, "not_present_in_index"
    type_result = run_git(root, "cat-file", "-t", object_id)
    if type_result.returncode != 0 or type_result.stdout.strip() != "blob":
        return None, "not_a_blob"
    size_result = run_git(root, "cat-file", "-s", object_id)
    if size_result.returncode != 0 or not size_result.stdout.strip().isdigit():
        return None, "blob_size_unavailable"
    return _Source(root, "index", repository_path, object_id, int(size_result.stdout), None), None


def _open_source(source: _Source) -> tuple[IO[bytes], subprocess.Popen[bytes] | None]:
    if source.worktree_path is not None:
        return source.worktree_path.open("rb"), None
    process = subprocess.Popen(  # noqa: S603 - hardened absolute Git command
        git_command(source.root, "cat-file", "blob", source.object_id),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if process.stdout is None:
        process.kill()
        raise ValueError("Git blob stream was not available")
    return process.stdout, process


def _close_source(stream: IO[bytes], process: subprocess.Popen[bytes] | None) -> None:
    stream.close()
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    if process.stderr is not None:
        process.stderr.close()


def _probe_source(source: _Source) -> bytes:
    stream, process = _open_source(source)
    try:
        return stream.read(min(_PROBE_BYTES, source.size_bytes))
    finally:
        _close_source(stream, process)


def _read_byte_page(source: _Source, start: int, end: int) -> _Page:
    stream, process = _open_source(source)
    deadline = time.monotonic() + git_timeout_seconds()
    try:
        remaining_skip = start
        while remaining_skip:
            if time.monotonic() > deadline:
                return _Page(
                    b"", start, start, None, False, None, {"next_byte": start}, "read_timeout"
                )
            chunk = stream.read(min(_READ_CHUNK, remaining_skip))
            if not chunk:
                return _Page(b"", start, start, None, True, None, None)
            remaining_skip -= len(chunk)
        content = bytearray()
        requested = end - start
        while len(content) < requested:
            if time.monotonic() > deadline:
                actual_end = start + len(content)
                return _Page(
                    bytes(content),
                    start,
                    actual_end,
                    None,
                    False,
                    None,
                    {"next_byte": actual_end},
                    "read_timeout",
                )
            chunk = stream.read(min(_READ_CHUNK, requested - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        actual_end = start + len(content)
        reached_eof = actual_end >= source.size_bytes
        return _Page(
            bytes(content),
            start,
            actual_end,
            None,
            reached_eof,
            None,
            None if reached_eof else {"next_byte": actual_end},
        )
    finally:
        _close_source(stream, process)


def _read_line_page(source: _Source, start: int, end: int) -> _Page:
    stream, process = _open_source(source)
    deadline = time.monotonic() + git_timeout_seconds()
    output = bytearray()
    line_number = 1
    byte_offset = 0
    selected_start: int | None = None
    selected_end = 0
    last_selected_line: int | None = None
    final_byte_was_newline = False
    try:
        while True:
            if time.monotonic() > deadline:
                return _Page(
                    bytes(output),
                    selected_start if selected_start is not None else byte_offset,
                    selected_end or byte_offset,
                    last_selected_line,
                    False,
                    None,
                    {"next_byte": selected_end or byte_offset, "next_line": line_number},
                    "read_timeout",
                )
            part = stream.readline(_READ_CHUNK)
            if not part:
                break
            part_start = byte_offset
            byte_offset += len(part)
            final_byte_was_newline = part.endswith(b"\n")
            if start <= line_number <= end:
                if selected_start is None:
                    selected_start = part_start
                remaining = _MAX_RESPONSE_BYTES - len(output)
                if len(part) > remaining:
                    output.extend(part[:remaining])
                    selected_end = part_start + remaining
                    return _Page(
                        bytes(output),
                        selected_start,
                        selected_end,
                        line_number,
                        False,
                        None,
                        {"next_byte": selected_end, "next_line": line_number},
                        "response_page_limit",
                    )
                output.extend(part)
                selected_end = byte_offset
                last_selected_line = line_number
            if part.endswith(b"\n"):
                completed_line = line_number
                line_number += 1
                if completed_line >= end:
                    probe = stream.read(1)
                    if probe:
                        return _Page(
                            bytes(output),
                            selected_start if selected_start is not None else selected_end,
                            selected_end,
                            last_selected_line,
                            False,
                            None,
                            {"next_line": line_number},
                        )
                    break
        total_lines = line_number - 1 if final_byte_was_newline else line_number
        if source.size_bytes == 0:
            total_lines = 0
        return _Page(
            bytes(output),
            selected_start if selected_start is not None else byte_offset,
            selected_end or byte_offset,
            last_selected_line,
            True,
            total_lines,
            None,
        )
    finally:
        _close_source(stream, process)


def _lfs_metadata(probe: bytes) -> dict[str, Any] | None:
    if probe.splitlines()[:1] != [_LFS_HEADER]:
        return None
    oid = _LFS_OID_RE.search(probe)
    declared_size = _LFS_SIZE_RE.search(probe)
    return {
        "content_kind": "git_lfs_pointer",
        "available": False,
        "unavailable_reason": "git_lfs_object_not_loaded",
        "lfs_object_id": oid.group(1).decode("ascii") if oid else None,
        "lfs_declared_size": int(declared_size.group(1)) if declared_size else None,
    }


def _write_git_file(
    project_root: Path,
    revision: str,
    path: str,
    *,
    start_line: int,
    end_line: int | None,
    start_byte: int | None,
    end_byte: int | None,
    output_path: Path | None,
) -> GitNavigationResult:
    root = validated_git_root(project_root)
    repository_path = safe_repository_path(path)
    rejection = EvidencePathPolicy.load(root).reason(repository_path)
    if rejection is not None:
        raise ValueError(f"evidence path rejected: {rejection}")
    byte_mode = start_byte is not None or end_byte is not None
    if byte_mode:
        if start_byte is None or end_byte is None or start_byte < 0 or end_byte <= start_byte:
            raise ValueError("byte range must be START:END with 0 <= START < END")
        if end_byte - start_byte > _MAX_RESPONSE_BYTES:
            raise ValueError(f"at most {_MAX_RESPONSE_BYTES} bytes may be returned at once")
    else:
        if start_line < 1:
            raise ValueError("start line must be at least 1")
        end_line = end_line if end_line is not None else start_line + DEFAULT_SOURCE_LINES - 1
        if end_line < start_line:
            raise ValueError("end line must not be before start line")
        if end_line - start_line + 1 > DEFAULT_SOURCE_LINES:
            raise ValueError(f"at most {DEFAULT_SOURCE_LINES} lines may be read at once")
    if revision == "worktree":
        resolved_revision = "worktree"
        source, reason = _worktree_source(root, repository_path)
    elif revision == "index":
        resolved_revision = "index"
        source, reason = _index_source(root, repository_path)
    else:
        resolved_revision = resolve_revision(root, revision)
        source, reason = _revision_source(root, resolved_revision, repository_path)
    object_id = source.object_id if source else None
    range_key = f"bytes:{start_byte}:{end_byte}" if byte_mode else f"lines:{start_line}:{end_line}"
    record_id = stable_navigation_id(
        "git-file", resolved_revision, repository_path, range_key, object_id or "unavailable"
    )
    payload: dict[str, Any] = {
        "schema_version": "v0",
        "record_type": "git_file",
        "record_id": record_id,
        "revision": resolved_revision,
        "path": repository_path,
        "object_id": object_id,
        "available": False,
        "reached_eof": False,
        "truncated": False,
    }
    if source is None:
        payload.update(content_kind="unavailable", unavailable_reason=reason or "unavailable")
    else:
        payload["size_bytes"] = source.size_bytes
        probe = _probe_source(source)
        lfs = _lfs_metadata(probe)
        if lfs is not None:
            payload.update(lfs)
        elif b"\0" in probe:
            payload.update(content_kind="binary", unavailable_reason="binary")
        else:
            if byte_mode:
                assert start_byte is not None and end_byte is not None
                page = _read_byte_page(source, start_byte, end_byte)
            else:
                assert end_line is not None
                page = _read_line_page(source, start_line, end_line)
            if source.worktree_path is not None:
                final_object_id = _hash_worktree(root, repository_path)
                if final_object_id != source.object_id:
                    payload.update(
                        content_kind="unavailable",
                        unavailable_reason="content_changed_during_read",
                        object_id_after_read=final_object_id,
                    )
                    page = None
            elif resolved_revision == "index":
                final_object_id = _index_object_id(root, repository_path)
                if final_object_id != source.object_id:
                    payload.update(
                        content_kind="unavailable",
                        unavailable_reason="content_changed_during_read",
                        object_id_after_read=final_object_id,
                    )
                    page = None
            if page is not None:
                payload.update(
                    {
                        "content_kind": "text",
                        "available": True,
                        "range": (
                            {"start_byte": page.start_byte, "end_byte": page.end_byte}
                            if byte_mode
                            else {"start_line": start_line, "end_line": page.end_line}
                        ),
                        "byte_range": {"start": page.start_byte, "end": page.end_byte},
                        "reached_eof": page.reached_eof,
                        "total_lines": page.total_lines,
                        "continuation": page.continuation,
                        "interrupted_reason": page.interrupted_reason,
                        "truncated": not page.reached_eof or start_line > 1 or bool(start_byte),
                        "content_bytes": len(page.content),
                        "locator": {
                            "revision": resolved_revision,
                            "path": repository_path,
                            "object_id": source.object_id,
                            "record_id": record_id,
                            "start_byte": page.start_byte,
                            "end_byte": page.end_byte,
                        },
                    }
                )
                try:
                    payload["content"] = page.content.decode("utf-8")
                except UnicodeDecodeError:
                    payload["content_kind"] = "non_utf8"
                    payload["unavailable_reason"] = "non_utf8_text_decode"
                    payload["content_base64"] = base64.b64encode(page.content).decode("ascii")
                    payload["encoding"] = "unknown"
    artifact_name = hashlib.sha256(
        f"{resolved_revision}\0{repository_path}\0{range_key}".encode()
    ).hexdigest()[:24]
    revision_dir = (
        resolved_revision[:12]
        if resolved_revision not in {"worktree", "index"}
        else resolved_revision
    )
    destination = local_navigation_output(
        root,
        output_path,
        root
        / ".learntrace"
        / "evidence"
        / "git"
        / "files"
        / revision_dir
        / f"{artifact_name}.json",
    )
    write_navigation_json(destination, payload)
    register_generated_artifacts(root, (destination,))
    return GitNavigationResult(destination, record_id, 1 if payload["available"] else 0)


def write_git_file(
    project_root: Path,
    revision: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    start_byte: int | None = None,
    end_byte: int | None = None,
    output_path: Path | None = None,
) -> GitNavigationResult:
    """Write one repeatable line or byte page without loading the full file."""
    try:
        return _write_git_file(
            project_root,
            revision,
            path,
            start_line=start_line,
            end_line=end_line,
            start_byte=start_byte,
            end_byte=end_byte,
            output_path=output_path,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git file read timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error
