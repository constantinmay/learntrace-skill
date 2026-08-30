"""候选静态材料发现；只列出路径，不读取文件内容。"""

from __future__ import annotations

import os
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from learntrace.artifacts import EvidencePathPolicy
from learntrace.parsers._common import project_reference, repo_root, safe_os_error
from learntrace.parsers.git import GitAuthor, list_git_authors
from learntrace.parsers.types import ParseWarning, ProjectInventory

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
    warnings: tuple[ParseWarning, ...] = ()
    git_authors: tuple[GitAuthor, ...] = ()


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


def discover_static_materials(
    project_root: Path,
    *,
    excluded_paths: Iterable[Path] = (),
) -> DiscoveredMaterials:
    """发现候选文件；调用方必须确认后再传给具体解析函数。"""
    root = repo_root(project_root)
    path_policy = EvidencePathPolicy.load(root, additional_generated=excluded_paths)
    excluded_references: set[str] = set()
    for requested in excluded_paths:
        candidate = requested if requested.is_absolute() else root / requested
        try:
            excluded_references.add(candidate.resolve(strict=False).relative_to(root).as_posix())
        except ValueError:
            continue
    documents: list[Path] = []
    test_logs: list[Path] = []
    files: list[Path] = []
    source_files: list[Path] = []
    test_files: list[Path] = []
    task_documents: list[Path] = []
    report_documents: list[Path] = []
    design_documents: list[Path] = []
    warnings: list[ParseWarning] = []
    extensions: Counter[str] = Counter()
    excluded_generated: set[str] = set(path_policy.generated_paths)

    def record_walk_error(error: OSError) -> None:
        filename = error.filename
        source = (
            project_reference(root, Path(os.fsdecode(filename))) if filename is not None else "."
        )
        warnings.append(ParseWarning("discovery_error", source, safe_os_error(error)))

    for directory, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=record_walk_error,
    ):
        current = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and name not in _EXCLUDED_DIRS
            and not (current / name).is_symlink()
            and path_policy.allows((current / name).relative_to(root))
        )
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            path = current / filename
            if path.is_symlink():
                continue
            relative = path.relative_to(root)
            reason = path_policy.reason(relative)
            if reason == "learntrace_generated_artifact":
                excluded_generated.add(relative.as_posix())
                continue
            if reason is not None:
                continue
            if relative.as_posix() in excluded_references:
                continue
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
    tracked_files: tuple[str, ...] = ()
    metadata_only_files: tuple[str, ...] = ()
    git_marker = root / ".git"
    # A plain directory named .git is not necessarily a repository (and is used
    # by callers/tests as a discovery marker). Only invoke Git when its control
    # file is present or when this is a linked worktree.
    if has_git and ((git_marker / "HEAD").is_file() or git_marker.is_file()):
        try:
            from learntrace.parsers.git import run_git, validated_git_root
            from learntrace.parsers.git_tree import read_tree_entries

            git_root = validated_git_root(root)
            head = run_git(git_root, "rev-parse", "--verify", "HEAD")
            if head.returncode != 0 or not head.stdout.strip():
                raise ValueError("Git history has no commits")
            _, tracked_entries = read_tree_entries(git_root, head.stdout.strip())
            tracked_files = tuple(
                sorted(
                    entry["path"] for entry in tracked_entries if isinstance(entry.get("path"), str)
                )
            )
            metadata_only_files = tuple(
                sorted(
                    entry["path"]
                    for entry in tracked_entries
                    if isinstance(entry.get("path"), str) and not entry.get("available", False)
                )
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            warnings.append(
                ParseWarning(
                    "tracked_inventory_unavailable",
                    ".",
                    safe_os_error(error)
                    if isinstance(error, OSError)
                    else "Git tracked-file inventory timed out"
                    if isinstance(error, subprocess.TimeoutExpired)
                    else str(error),
                )
            )
    git_authors = list_git_authors(root) if has_git else ()
    sorted_documents = sort_paths(documents)
    sorted_logs = sort_paths(test_logs)
    inventory = ProjectInventory(
        git_available=has_git,
        tracked_files=tracked_files,
        metadata_only_files=metadata_only_files,
        files=tuple(path.as_posix() for path in sort_paths(files)),
        source_files=tuple(path.as_posix() for path in sort_paths(source_files)),
        test_files=tuple(path.as_posix() for path in sort_paths(test_files)),
        documents=tuple(path.as_posix() for path in sorted_documents),
        test_logs=tuple(path.as_posix() for path in sorted_logs),
        task_documents=tuple(path.as_posix() for path in sort_paths(task_documents)),
        report_documents=tuple(path.as_posix() for path in sort_paths(report_documents)),
        design_documents=tuple(path.as_posix() for path in sort_paths(design_documents)),
        extension_counts=tuple(sorted(extensions.items())),
        excluded_generated_artifacts=tuple(sorted(excluded_generated)),
    )
    return DiscoveredMaterials(
        documents=sorted_documents,
        test_logs=sorted_logs,
        has_git=has_git,
        inventory=inventory,
        warnings=tuple(warnings),
        git_authors=git_authors,
    )
