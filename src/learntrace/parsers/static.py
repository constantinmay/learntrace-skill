"""静态材料解析的组合入口。"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from learntrace.parsers._common import repo_root
from learntrace.parsers.documents import parse_documents
from learntrace.parsers.git import parse_git_history
from learntrace.parsers.test_logs import parse_test_logs
from learntrace.parsers.types import ParseResult


def parse_static_materials(
    project_root: Path,
    *,
    document_paths: Iterable[Path],
    test_log_paths: Iterable[Path],
    include_git: bool = True,
    max_commits: int = 50,
) -> ParseResult:
    """解析调用方已确认的范围，返回事实事件和非致命告警。"""
    repo_root(project_root)
    result = ParseResult()
    if include_git:
        result = result.merged(parse_git_history(project_root, max_commits=max_commits))
    return result.merged(
        parse_documents(project_root, document_paths),
        parse_test_logs(project_root, test_log_paths),
    )
