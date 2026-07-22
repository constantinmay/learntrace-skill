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
    max_commits: int = 50,
) -> ParseResult:
    """解析调用方已确认的范围，返回事实事件和非致命告警。"""
    root = repo_root(project_root)
    documents = deduplicate_paths(root, document_paths)
    logs = deduplicate_paths(root, test_log_paths)
    inventory = discover_static_materials(root).inventory
    scope = AnalysisScope(
        include_git=include_git,
        documents=tuple(project_reference(root, path) for path in documents),
        test_logs=tuple(project_reference(root, path) for path in logs),
    )
    result = ParseResult()
    if include_git:
        result = result.merged(parse_git_history(root, max_commits=max_commits))
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
