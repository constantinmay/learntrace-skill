"""静态解析器共享的路径、文本和标识工具。"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable
from pathlib import Path

MAX_TEXT_FILE_BYTES = 1_048_576


def repo_root(path: Path) -> Path:
    root = path.resolve()
    if not root.is_dir():
        msg = f"project root is not a directory: {path}"
        raise ValueError(msg)
    return root


def resolve_project_file(root: Path, path: Path) -> tuple[Path, str]:
    """解析项目内文件，并返回绝对路径和 POSIX 相对路径。"""
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        msg = "path is outside project root"
        raise ValueError(msg) from error
    if not candidate.is_file():
        msg = "source is not a file"
        raise ValueError(msg)
    return candidate, relative.as_posix()


def project_reference(root: Path, path: Path) -> str:
    """返回不泄漏项目外绝对目录的显示引用。"""
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return f"[outside-project]/{path.name}"


def deduplicate_paths(root: Path, paths: Iterable[Path]) -> tuple[Path, ...]:
    """按解析后的本地路径去重，同时保留调用方顺序。"""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return tuple(unique)


def stable_event_id(kind: str, *parts: str) -> str:
    value = "\x1f".join((kind, *parts)).encode("utf-8")
    digest = hashlib.sha256(value).hexdigest()[:16]
    return f"evt-{kind}-{digest}"


def safe_os_error(error: OSError) -> str:
    """保留可诊断错误类别，但不把本机绝对路径写入输出。"""
    detail = error.strerror or type(error).__name__
    return f"{detail} (errno {error.errno})" if error.errno is not None else detail


def compact_text(value: str, *, limit: int | None = None) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if limit is None or len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"
