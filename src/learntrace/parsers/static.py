"""静态材料解析的组合入口。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from learntrace.parsers._common import (
    deduplicate_paths,
    project_reference,
    repo_root,
)
from learntrace.parsers.discovery import discover_static_materials
from learntrace.parsers.documents import parse_documents
from learntrace.parsers.git import parse_git_history
from learntrace.parsers.test_logs import parse_test_logs
from learntrace.parsers.types import AnalysisScope, ParseResult


def parse_static_materials(
    project_root: Path,
    *,
    document_paths: Iterable[Path],
    test_log_paths: Iterable[Path],
    include_git: bool = True,
    max_commits: int | None = None,
    find_copies_harder: bool = False,
    inventory_excluded_paths: Iterable[Path] = (),
) -> ParseResult:
    """解析调用方已确认的范围，返回事实事件和非致命告警。"""
    root = repo_root(project_root)
    documents = deduplicate_paths(root, document_paths)
    logs = deduplicate_paths(root, test_log_paths)
    excluded = tuple(inventory_excluded_paths)
    discovered = (
        discover_static_materials(root, excluded_paths=excluded)
        if excluded
        else discover_static_materials(root)
    )
    inventory = discovered.inventory
    scope = AnalysisScope(
        include_git=include_git,
        documents=tuple(project_reference(root, path) for path in documents),
        test_logs=tuple(project_reference(root, path) for path in logs),
    )
    result = ParseResult(warnings=discovered.warnings)
    if include_git:
        result = result.merged(
            parse_git_history(
                root,
                max_commits=max_commits,
                find_copies_harder=find_copies_harder,
                excluded_paths=excluded,
            )
        )
    combined = result.merged(
        parse_documents(root, documents),
        parse_test_logs(root, logs),
    )
    return ParseResult(
        events=combined.events,
        warnings=combined.warnings,
        scope=scope,
        inventory=inventory,
    )
