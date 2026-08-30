"""Complete local Git version trees and compact layout summaries."""

from __future__ import annotations

import base64
import re
import subprocess
from pathlib import Path
from typing import Any

from learntrace.artifacts import EvidencePathPolicy, register_generated_artifacts
from learntrace.parsers._common import compact_text, safe_os_error
from learntrace.parsers.git import run_git, run_git_bytes, validated_git_root
from learntrace.parsers.git_navigation import (
    GitNavigationResult,
    classify_content_kind,
    file_roles,
    local_navigation_output,
    resolve_revision,
    stable_navigation_id,
    write_navigation_json,
)

_TREE_ENTRY_RE = re.compile(rb"^([0-9]{6}) ([a-z]+) ([0-9a-f]+) +(-|[0-9]+)\t(.*)$")


def _decode_path(raw_path: bytes) -> tuple[str, dict[str, str]]:
    try:
        return raw_path.decode("utf-8"), {}
    except UnicodeDecodeError:
        return (
            raw_path.decode("utf-8", errors="backslashreplace"),
            {
                "path_encoding": "non_utf8",
                "path_bytes_base64": base64.b64encode(raw_path).decode("ascii"),
            },
        )


def _read_tree_entries(
    root: Path,
    commit_id: str,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    tree_result = run_git(root, "show", "-s", "--format=%T", commit_id)
    if tree_result.returncode != 0 or not tree_result.stdout.strip():
        raise ValueError(compact_text(tree_result.stderr) or "could not read commit tree")
    tree_id = tree_result.stdout.strip()
    result = run_git_bytes(root, "ls-tree", "-r", "-l", "-z", tree_id)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace")
        raise ValueError(compact_text(message) or "could not read Git tree")
    policy = EvidencePathPolicy.load(root)
    entries: list[dict[str, Any]] = []
    excluded: list[str] = []
    for raw_entry in result.stdout.split(b"\0"):
        if not raw_entry:
            continue
        match = _TREE_ENTRY_RE.fullmatch(raw_entry)
        if match is None:
            path, path_metadata = _decode_path(raw_entry)
            entries.append(
                {
                    "path": path,
                    **path_metadata,
                    "available": False,
                    "unavailable_reason": "malformed_git_tree_entry",
                }
            )
            continue
        mode, object_type, object_id, raw_size, raw_path = match.groups()
        path, path_metadata = _decode_path(raw_path)
        reason = policy.reason(path)
        if reason == "learntrace_generated_artifact":
            excluded.append(path)
            continue
        if reason is not None:
            continue
        type_text = object_type.decode("ascii")
        if path_metadata:
            kind, available, unavailable_reason = (
                "unknown",
                False,
                "non_utf8_repository_path",
            )
        else:
            kind, available, unavailable_reason = classify_content_kind(path, type_text)
        entry: dict[str, Any] = {
            "path": path,
            **path_metadata,
            "mode": mode.decode("ascii"),
            "object_type": type_text,
            "object_id": object_id.decode("ascii"),
            "size": None if raw_size == b"-" else int(raw_size),
            "content_kind": kind,
            "roles": [] if path_metadata else file_roles(path),
            "available": available,
        }
        if unavailable_reason is not None:
            entry["unavailable_reason"] = unavailable_reason
        entries.append(entry)
    return tree_id, entries, excluded


def read_tree_entries(root: Path, commit_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Return evidence-eligible entries without scanning blob contents."""
    tree_id, entries, _ = _read_tree_entries(root, commit_id)
    return tree_id, entries


def tree_summary(
    root: Path,
    revision: str,
    *,
    cache: dict[str, tuple[str, int, dict[str, int]]] | None = None,
) -> tuple[str, int, dict[str, int]]:
    """Return a compact version layout, reusing identical commit trees."""
    commit_id = resolve_revision(root, revision)
    tree_id_result = run_git(root, "show", "-s", "--format=%T", commit_id)
    if tree_id_result.returncode != 0 or not tree_id_result.stdout.strip():
        raise ValueError(compact_text(tree_id_result.stderr) or "could not read commit tree")
    tree_id = tree_id_result.stdout.strip()
    if cache is not None and tree_id in cache:
        return cache[tree_id]
    actual_tree_id, entries, _ = _read_tree_entries(root, commit_id)
    top_level: dict[str, int] = {}
    for entry in entries:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        top = path.split("/", maxsplit=1)[0]
        top_level[top] = top_level.get(top, 0) + 1
    summary = actual_tree_id, len(entries), dict(sorted(top_level.items()))
    if cache is not None:
        cache[tree_id] = summary
    return summary


def _write_git_tree(
    project_root: Path,
    revision: str,
    output_path: Path | None,
) -> GitNavigationResult:
    root = validated_git_root(project_root)
    commit_id = resolve_revision(root, revision)
    tree_id, entries, excluded = _read_tree_entries(root, commit_id)
    record_id = stable_navigation_id("git-tree", commit_id, tree_id)
    top_level: dict[str, int] = {}
    for entry in entries:
        path = entry.get("path")
        if isinstance(path, str) and path:
            top = path.split("/", maxsplit=1)[0]
            top_level[top] = top_level.get(top, 0) + 1
    payload = {
        "schema_version": "v0",
        "record_type": "git_tree",
        "record_id": record_id,
        "revision": commit_id,
        "tree_id": tree_id,
        "file_count": len(entries),
        "top_level_counts": dict(sorted(top_level.items())),
        "entries": entries,
        "excluded_generated_artifacts": {
            "count": len(excluded),
            "paths": sorted(excluded),
        },
    }
    destination = local_navigation_output(
        root,
        output_path,
        root / ".learntrace" / "evidence" / "git" / "trees" / f"{tree_id}.json",
    )
    write_navigation_json(destination, payload)
    register_generated_artifacts(root, (destination,))
    return GitNavigationResult(destination, record_id, len(entries))


def write_git_tree(
    project_root: Path,
    revision: str,
    output_path: Path | None = None,
) -> GitNavigationResult:
    """Write the complete tracked tree for one reachable local revision."""
    try:
        return _write_git_tree(project_root, revision, output_path)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git tree read timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error
