"""Agent-facing local Git navigation and source readback.

This module deliberately returns locating facts rather than interpreting code.
All evidence stays inside the inspected repository's ``.learntrace`` directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from learntrace.parsers.git import path_is_within, run_git

DEFAULT_SOURCE_LINES = 200
_MAX_REVISION_LENGTH = 200
_BINARY_SUFFIXES = frozenset(
    {
        ".7z",
        ".a",
        ".avi",
        ".bin",
        ".bmp",
        ".class",
        ".db",
        ".dll",
        ".doc",
        ".docx",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jar",
        ".jpeg",
        ".jpg",
        ".mov",
        ".mp3",
        ".mp4",
        ".o",
        ".pdf",
        ".png",
        ".pyc",
        ".so",
        ".sqlite",
        ".sqlite3",
        ".tar",
        ".wasm",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
_SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".kts",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".sh",
        ".sql",
        ".swift",
        ".ts",
        ".tsx",
    }
)
_TEXT_SUFFIXES = _SOURCE_SUFFIXES | frozenset(
    {
        ".cfg",
        ".conf",
        ".css",
        ".csv",
        ".env",
        ".graphql",
        ".htm",
        ".html",
        ".ini",
        ".json",
        ".jsonl",
        ".lock",
        ".log",
        ".md",
        ".rst",
        ".svg",
        ".toml",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


@dataclass(frozen=True, slots=True)
class GitNavigationResult:
    """Location and basic counts for a written navigation artifact."""

    output_path: Path
    record_id: str
    item_count: int


def stable_navigation_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join((kind, *parts)).encode("utf-8")).hexdigest()[:24]
    return f"{kind}-{digest}"


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def write_navigation_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def local_navigation_output(root: Path, requested: Path | None, default: Path) -> Path:
    destination = requested or default
    destination = destination if destination.is_absolute() else root / destination
    resolved = destination.resolve(strict=False)
    if not path_is_within(resolved, root):
        raise ValueError("Git navigation output must stay inside the project root")
    return resolved


def resolve_revision(root: Path, revision: str) -> str:
    if (
        not revision
        or len(revision) > _MAX_REVISION_LENGTH
        or revision.startswith("-")
        or any(ord(character) < 32 for character in revision)
    ):
        raise ValueError("invalid Git revision")
    result = run_git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}^{{commit}}",
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("revision does not identify a commit in this repository")
    commit_id = result.stdout.strip()
    reachable = run_git(root, "merge-base", "--is-ancestor", commit_id, "HEAD")
    if reachable.returncode != 0:
        raise ValueError("revision is not reachable from HEAD")
    return commit_id


def file_roles(path: str) -> list[str]:
    """Return deterministic navigation roles without claiming semantic meaning."""
    relative = Path(path)
    lowered = path.lower()
    parts = {part.lower() for part in relative.parts[:-1]}
    stem = relative.stem.lower()
    suffix = relative.suffix.lower()
    roles: list[str] = []
    is_test = bool(parts & {"test", "tests", "spec", "specs", "__tests__"}) or (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )
    if is_test:
        roles.append("test")
    elif suffix in _SOURCE_SUFFIXES:
        roles.append("source")
    if suffix == ".log" or "test-result" in lowered or "test_result" in lowered:
        roles.append("log")
    if suffix in {".md", ".rst", ".txt"}:
        roles.append("document")
    if relative.name.lower() in {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "dockerfile",
        "makefile",
    } or suffix in {".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"}:
        roles.append("configuration")
    return roles or ["other"]


def classify_content_kind(path: str, object_type: str) -> tuple[str, bool, str | None]:
    if object_type == "commit":
        return "submodule", False, "submodule_content_not_in_parent_repository"
    suffix = Path(path).suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        return "binary", False, "binary_content_requires_external_reader"
    if suffix in _TEXT_SUFFIXES or Path(path).name.lower() in {
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        "dockerfile",
        "makefile",
    }:
        return "text", True, None
    return "unknown", True, None
