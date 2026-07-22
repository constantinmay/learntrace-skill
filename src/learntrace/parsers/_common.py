"""静态解析器共享的路径、文本和标识工具。"""

from __future__ import annotations

import hashlib
import re
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
        msg = f"path is outside project root: {path}"
        raise ValueError(msg) from error
    if not candidate.is_file():
        msg = f"source is not a file: {path}"
        raise ValueError(msg)
    return candidate, relative.as_posix()


def stable_event_id(kind: str, *parts: str) -> str:
    value = "\x1f".join((kind, *parts)).encode("utf-8")
    digest = hashlib.sha256(value).hexdigest()[:16]
    return f"evt-{kind}-{digest}"


def compact_text(value: str, *, limit: int = 300) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}…"
