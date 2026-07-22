"""候选静态材料发现；只列出路径，不读取文件内容。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from learntrace.parsers._common import repo_root

_EXCLUDED_DIRS = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
)
_TEST_LOG_NAMES = ("pytest", "test-result", "test_result", "test-log", "test_log")


@dataclass(frozen=True, slots=True)
class DiscoveredMaterials:
    """等待用户确认分析范围的仓库相对路径。"""

    documents: tuple[Path, ...]
    test_logs: tuple[Path, ...]
    has_git: bool


def _is_test_log(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".log" or (
        path.suffix.lower() == ".txt" and any(marker in name for marker in _TEST_LOG_NAMES)
    )


def discover_static_materials(project_root: Path) -> DiscoveredMaterials:
    """发现候选文件；调用方必须确认后再传给具体解析函数。"""
    root = repo_root(project_root)
    documents: list[Path] = []
    test_logs: list[Path] = []

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and name not in _EXCLUDED_DIRS
            and not (current / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = current / filename
            if path.is_symlink():
                continue
            relative = path.relative_to(root)
            if _is_test_log(path):
                test_logs.append(relative)
            elif path.suffix.lower() in {".md", ".txt"}:
                documents.append(relative)

    def path_key(value: Path) -> str:
        return value.as_posix()

    return DiscoveredMaterials(
        documents=tuple(sorted(documents, key=path_key)),
        test_logs=tuple(sorted(test_logs, key=path_key)),
        has_git=(root / ".git").exists(),
    )
