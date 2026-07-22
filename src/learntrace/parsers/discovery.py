"""候选静态材料发现；只列出路径，不读取文件内容。"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from learntrace.parsers._common import repo_root
from learntrace.parsers.types import ProjectInventory

_EXCLUDED_DIRS = frozenset(
    {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
)
_TEST_LOG_NAMES = ("pytest", "test-result", "test_result", "test-log", "test_log")
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
_TASK_MARKERS = ("assignment", "requirement", "task", "作业", "任务", "要求")
_REPORT_MARKERS = ("report", "portfolio", "报告", "总结")
_DESIGN_MARKERS = ("architecture", "design", "proposal", "架构", "设计", "方案")


@dataclass(frozen=True, slots=True)
class DiscoveredMaterials:
    """等待用户确认分析范围的仓库相对路径。"""

    documents: tuple[Path, ...]
    test_logs: tuple[Path, ...]
    has_git: bool
    inventory: ProjectInventory


def _is_test_log(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() == ".log" or (
        path.suffix.lower() == ".txt" and any(marker in name for marker in _TEST_LOG_NAMES)
    )


def _is_test_file(relative: Path) -> bool:
    lower_parts = {part.lower() for part in relative.parts[:-1]}
    stem = relative.stem.lower()
    return bool(lower_parts & {"test", "tests", "spec", "specs", "__tests__"}) or (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


def _matches_any(relative: Path, markers: tuple[str, ...]) -> bool:
    value = relative.as_posix().lower()
    return any(marker in value for marker in markers)


def discover_static_materials(project_root: Path) -> DiscoveredMaterials:
    """发现候选文件；调用方必须确认后再传给具体解析函数。"""
    root = repo_root(project_root)
    documents: list[Path] = []
    test_logs: list[Path] = []
    files: list[Path] = []
    source_files: list[Path] = []
    test_files: list[Path] = []
    task_documents: list[Path] = []
    report_documents: list[Path] = []
    design_documents: list[Path] = []
    extensions: Counter[str] = Counter()

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
            if filename.startswith("."):
                continue
            path = current / filename
            if path.is_symlink():
                continue
            relative = path.relative_to(root)
            files.append(relative)
            suffix = path.suffix.lower() or "[no extension]"
            extensions[suffix] += 1
            if suffix in _SOURCE_SUFFIXES:
                source_files.append(relative)
            if _is_test_file(relative):
                test_files.append(relative)
            if _is_test_log(path):
                test_logs.append(relative)
            elif path.suffix.lower() in {".md", ".txt"}:
                documents.append(relative)
                if _matches_any(relative, _TASK_MARKERS):
                    task_documents.append(relative)
                if _matches_any(relative, _REPORT_MARKERS):
                    report_documents.append(relative)
                if _matches_any(relative, _DESIGN_MARKERS):
                    design_documents.append(relative)

    def path_key(value: Path) -> str:
        return value.as_posix()

    def sort_paths(values: list[Path]) -> tuple[Path, ...]:
        return tuple(sorted(values, key=path_key))

    has_git = (root / ".git").exists()
    sorted_documents = sort_paths(documents)
    sorted_logs = sort_paths(test_logs)
    inventory = ProjectInventory(
        git_available=has_git,
        files=tuple(path.as_posix() for path in sort_paths(files)),
        source_files=tuple(path.as_posix() for path in sort_paths(source_files)),
        test_files=tuple(path.as_posix() for path in sort_paths(test_files)),
        documents=tuple(path.as_posix() for path in sorted_documents),
        test_logs=tuple(path.as_posix() for path in sorted_logs),
        task_documents=tuple(path.as_posix() for path in sort_paths(task_documents)),
        report_documents=tuple(path.as_posix() for path in sort_paths(report_documents)),
        design_documents=tuple(path.as_posix() for path in sort_paths(design_documents)),
        extension_counts=tuple(sorted(extensions.items())),
    )
    return DiscoveredMaterials(
        documents=sorted_documents,
        test_logs=sorted_logs,
        has_git=has_git,
        inventory=inventory,
    )
