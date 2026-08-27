"""Staged, unstaged, untracked, rename, deletion, and conflict evidence."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from learntrace.parsers._common import compact_text, safe_os_error
from learntrace.parsers.git import DEFAULT_EVIDENCE_MAX_CHARS, run_git, validated_git_root
from learntrace.parsers.git_navigation import (
    GitNavigationResult,
    file_roles,
    local_navigation_output,
    stable_navigation_id,
    write_navigation_json,
)

_LOCAL_EVIDENCE_PATHS = (
    "--",
    ".",
    ":(exclude).learntrace",
    ":(exclude).learntrace/**",
    ":(exclude)learning-record.md",
)


def _parse_porcelain_z(value: str) -> list[dict[str, Any]]:
    tokens = value.split("\0")
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 4 or token[2] != " ":
            entries.append(
                {
                    "path": token,
                    "available": False,
                    "unavailable_reason": "malformed_git_status_entry",
                }
            )
            continue
        x, y, path = token[0], token[1], token[3:]
        previous_path = None
        if (x in {"R", "C"} or y in {"R", "C"}) and index < len(tokens):
            previous_path = tokens[index]
            index += 1
        conflict = (
            x == "U"
            or y == "U"
            or (x, y)
            in {
                ("A", "A"),
                ("D", "D"),
                ("A", "U"),
                ("U", "D"),
                ("U", "A"),
                ("D", "U"),
            }
        )
        entries.append(
            {
                "path": path,
                "previous_path": previous_path,
                "index_status": x,
                "worktree_status": y,
                "staged": x not in {" ", "?"},
                "unstaged": y not in {" ", "?"},
                "untracked": x == "?" and y == "?",
                "conflict": conflict,
                "roles": file_roles(path),
            }
        )
    return entries


def _bounded_diff(root: Path, *arguments: str, max_chars: int) -> dict[str, Any]:
    result = run_git(root, *arguments, *_LOCAL_EVIDENCE_PATHS)
    if result.returncode != 0:
        return {
            "available": False,
            "unavailable_reason": compact_text(result.stderr) or "Git diff failed",
            "truncated": False,
        }
    return {
        "available": True,
        "content": result.stdout[:max_chars],
        "sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
        "source_chars": len(result.stdout),
        "written_chars": min(len(result.stdout), max_chars),
        "truncated": len(result.stdout) > max_chars,
    }


def _write_git_worktree(
    project_root: Path,
    *,
    output_path: Path | None,
    max_chars: int,
) -> GitNavigationResult:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    root = validated_git_root(project_root)
    status = run_git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        *_LOCAL_EVIDENCE_PATHS,
    )
    if status.returncode != 0:
        raise ValueError(compact_text(status.stderr) or "could not read Git worktree status")
    entries = _parse_porcelain_z(status.stdout)
    head = run_git(root, "rev-parse", "--verify", "HEAD")
    head_id = head.stdout.strip() if head.returncode == 0 else None
    staged_diff = _bounded_diff(
        root,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        max_chars=max_chars,
    )
    unstaged_diff = _bounded_diff(
        root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        max_chars=max_chars,
    )
    record_id = stable_navigation_id(
        "git-worktree",
        head_id or "no-head",
        status.stdout,
        str(staged_diff.get("sha256", staged_diff.get("unavailable_reason", ""))),
        str(unstaged_diff.get("sha256", unstaged_diff.get("unavailable_reason", ""))),
    )
    payload = {
        "schema_version": "v0",
        "record_type": "git_worktree",
        "record_id": record_id,
        "head": head_id,
        "entries": entries,
        "counts": {
            "total": len(entries),
            "staged": sum(bool(entry.get("staged")) for entry in entries),
            "unstaged": sum(bool(entry.get("unstaged")) for entry in entries),
            "untracked": sum(bool(entry.get("untracked")) for entry in entries),
            "conflicts": sum(bool(entry.get("conflict")) for entry in entries),
        },
        "staged_diff": staged_diff,
        "unstaged_diff": unstaged_diff,
        "untracked_readback": {
            "command": "learntrace git-file <project> worktree <path> --lines <start>:<end>",
            "content_included": False,
        },
    }
    destination = local_navigation_output(
        root,
        output_path,
        root / ".learntrace" / "evidence" / "git" / "worktree.json",
    )
    write_navigation_json(destination, payload)
    return GitNavigationResult(destination, record_id, len(entries))


def write_git_worktree(
    project_root: Path,
    *,
    output_path: Path | None = None,
    max_chars: int = DEFAULT_EVIDENCE_MAX_CHARS,
) -> GitNavigationResult:
    """Write staged, unstaged, untracked, rename, deletion, and conflict facts."""
    try:
        return _write_git_worktree(
            project_root,
            output_path=output_path,
            max_chars=max_chars,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git worktree read timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error
