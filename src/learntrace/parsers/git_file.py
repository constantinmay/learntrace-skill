"""Bounded source readback for historical revisions and the current worktree."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from learntrace.parsers._common import compact_text, safe_os_error
from learntrace.parsers.git import (
    path_is_within,
    run_git,
    run_git_bytes,
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


def _read_worktree_file(
    root: Path,
    repository_path: str,
) -> tuple[bytes | None, str | None, str | None]:
    candidate = root / Path(repository_path)
    try:
        if candidate.is_symlink():
            return None, None, "symlink_not_followed"
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, None, "not_present"
    except OSError as error:
        return None, None, safe_os_error(error)
    if not path_is_within(resolved, root):
        return None, None, "path_outside_project"
    if not resolved.is_file():
        return None, None, "not_a_file"
    try:
        content = resolved.read_bytes()
    except OSError as error:
        return None, None, safe_os_error(error)
    object_result = run_git(root, "hash-object", "--no-filters", "--", repository_path)
    object_id = object_result.stdout.strip() if object_result.returncode == 0 else None
    return content, object_id, None


def _read_revision_file(
    root: Path,
    commit_id: str,
    repository_path: str,
) -> tuple[bytes | None, str | None, str | None]:
    object_result = run_git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{commit_id}:{repository_path}",
    )
    if object_result.returncode != 0 or not object_result.stdout.strip():
        return None, None, "not_present"
    object_id = object_result.stdout.strip()
    type_result = run_git(root, "cat-file", "-t", object_id)
    if type_result.returncode != 0 or type_result.stdout.strip() != "blob":
        return None, object_id, "not_a_blob"
    content = run_git_bytes(root, "cat-file", "blob", object_id)
    if content.returncode != 0:
        message = content.stderr.decode("utf-8", errors="replace")
        return None, object_id, compact_text(message) or "blob_unavailable"
    return content.stdout, object_id, None


def _line_slice(text: str, start: int, end: int) -> tuple[str, int, int | None, bool]:
    lines = text.splitlines(keepends=True)
    total = len(lines)
    actual_end = min(end, total)
    if start > total:
        return "", total, None, True
    selected = "".join(lines[start - 1 : actual_end])
    return selected, total, actual_end, start > 1 or actual_end < total


def _write_git_file(
    project_root: Path,
    revision: str,
    path: str,
    *,
    start_line: int,
    end_line: int | None,
    output_path: Path | None,
) -> GitNavigationResult:
    if start_line < 1:
        raise ValueError("start line must be at least 1")
    requested_end = end_line if end_line is not None else start_line + DEFAULT_SOURCE_LINES - 1
    if requested_end < start_line:
        raise ValueError("end line must not be before start line")
    if requested_end - start_line + 1 > DEFAULT_SOURCE_LINES:
        raise ValueError(f"at most {DEFAULT_SOURCE_LINES} lines may be read at once")
    root = validated_git_root(project_root)
    repository_path = safe_repository_path(path)
    if revision == "worktree":
        resolved_revision = "worktree"
        content, object_id, reason = _read_worktree_file(root, repository_path)
    else:
        resolved_revision = resolve_revision(root, revision)
        content, object_id, reason = _read_revision_file(root, resolved_revision, repository_path)
    record_id = stable_navigation_id(
        "git-file",
        resolved_revision,
        repository_path,
        str(start_line),
        str(requested_end),
        object_id or "unavailable",
    )
    payload: dict[str, Any] = {
        "schema_version": "v0",
        "record_type": "git_file",
        "record_id": record_id,
        "revision": resolved_revision,
        "path": repository_path,
        "object_id": object_id,
        "requested_range": {"start": start_line, "end": requested_end},
        "available": False,
        "truncated": False,
    }
    if content is None:
        payload["content_kind"] = "unavailable"
        payload["unavailable_reason"] = reason or "unavailable"
    elif content.splitlines()[:1] == [_LFS_HEADER]:
        payload["content_kind"] = "git_lfs_pointer"
        payload["unavailable_reason"] = "git_lfs_object_not_loaded"
        payload["pointer"] = content.decode("utf-8", errors="replace")
    elif b"\0" in content:
        payload["content_kind"] = "binary"
        payload["unavailable_reason"] = "binary"
        payload["size"] = len(content)
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            payload["content_kind"] = "non_utf8"
            payload["unavailable_reason"] = "non_utf8"
            payload["size"] = len(content)
        else:
            selected, total_lines, actual_end, truncated = _line_slice(
                text,
                start_line,
                requested_end,
            )
            payload.update(
                {
                    "content_kind": "text",
                    "available": True,
                    "start_line": start_line,
                    "end_line": actual_end,
                    "total_lines": total_lines,
                    "truncated": truncated,
                    "content": selected,
                    "locator": {
                        "revision": resolved_revision,
                        "path": repository_path,
                        "start_line": start_line,
                        "end_line": actual_end,
                        "object_id": object_id,
                        "record_id": record_id,
                    },
                }
            )
    artifact_name = hashlib.sha256(
        f"{resolved_revision}\0{repository_path}\0{start_line}\0{requested_end}".encode()
    ).hexdigest()[:24]
    revision_dir = resolved_revision[:12] if resolved_revision != "worktree" else "worktree"
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
    return GitNavigationResult(destination, record_id, 1 if payload["available"] else 0)


def write_git_file(
    project_root: Path,
    revision: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
    output_path: Path | None = None,
) -> GitNavigationResult:
    """Write a bounded source range from a revision or the current worktree."""
    try:
        return _write_git_file(
            project_root,
            revision,
            path,
            start_line=start_line,
            end_line=end_line,
            output_path=output_path,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git file read timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error
