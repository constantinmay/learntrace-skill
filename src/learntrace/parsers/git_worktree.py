"""Staged, unstaged, untracked, rename, deletion, and conflict evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from learntrace.artifacts import EvidencePathPolicy, register_generated_artifacts
from learntrace.parsers._common import compact_text, safe_os_error
from learntrace.parsers.git import (
    DEFAULT_EVIDENCE_MAX_CHARS,
    run_git,
    stream_git_text_preview,
    validated_git_root,
)
from learntrace.parsers.git_navigation import (
    GitNavigationResult,
    file_roles,
    local_navigation_output,
    stable_navigation_id,
    write_navigation_json,
)

_BUILTIN_EVIDENCE_PATHS = (
    "--",
    ".",
    ":(exclude).learntrace",
    ":(exclude).learntrace/**",
    ":(exclude)learning-record.md",
    ":(glob,exclude)**/learning-record.md",
    ":(glob,exclude)**/learning-questions.md",
)


def _evidence_paths(policy: EvidencePathPolicy) -> tuple[str, ...]:
    custom = tuple(
        f":(exclude,literal){path}"
        for path in sorted(policy.generated_paths)
        if policy.reason(path) == "learntrace_generated_artifact"
    )
    return (*_BUILTIN_EVIDENCE_PATHS, *custom)


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


def _bounded_diff(
    root: Path,
    policy: EvidencePathPolicy,
    *arguments: str,
    max_chars: int,
) -> dict[str, Any]:
    return stream_git_text_preview(
        root,
        *arguments,
        *_evidence_paths(policy),
        max_chars=max_chars,
    )


def _write_git_worktree(
    project_root: Path,
    *,
    output_path: Path | None,
    max_chars: int,
) -> GitNavigationResult:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    root = validated_git_root(project_root)
    policy = EvidencePathPolicy.load(root)
    status = run_git(
        root,
        "-c",
        "core.fsmonitor=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--",
        ".",
    )
    if status.returncode != 0:
        raise ValueError(compact_text(status.stderr) or "could not read Git worktree status")
    parsed_entries = _parse_porcelain_z(status.stdout)
    excluded_paths: set[str] = set()
    for entry in parsed_entries:
        for key in ("path", "previous_path"):
            value = entry.get(key)
            if isinstance(value, str) and policy.reason(value) == "learntrace_generated_artifact":
                excluded_paths.add(value)
    excluded = sorted(excluded_paths)
    entries = [
        entry
        for entry in parsed_entries
        if all(
            not isinstance(entry.get(key), str) or policy.allows(str(entry[key]))
            for key in ("path", "previous_path")
        )
    ]
    head = run_git(root, "rev-parse", "--verify", "HEAD")
    head_id = head.stdout.strip() if head.returncode == 0 else None
    staged_diff = _bounded_diff(
        root,
        policy,
        "diff",
        "--cached",
        "--no-ext-diff",
        "--no-textconv",
        max_chars=max_chars,
    )
    unstaged_diff = _bounded_diff(
        root,
        policy,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        max_chars=max_chars,
    )
    record_id = stable_navigation_id(
        "git-worktree",
        head_id or "no-head",
        json.dumps(entries, ensure_ascii=False, sort_keys=True),
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
        "staged_readback": {
            "command": "learntrace git-file <project> index <path> --lines <start>:<end>",
            "content_included": False,
        },
        "excluded_generated_artifacts": {"count": len(excluded), "paths": excluded},
    }
    destination = local_navigation_output(
        root,
        output_path,
        root / ".learntrace" / "evidence" / "git" / "worktree.json",
    )
    write_navigation_json(destination, payload)
    register_generated_artifacts(root, (destination,))
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
