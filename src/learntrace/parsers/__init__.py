"""只读的本地项目静态材料解析器。"""

from learntrace.parsers.discovery import DiscoveredMaterials, discover_static_materials
from learntrace.parsers.documents import parse_documents
from learntrace.parsers.git import (
    DEFAULT_EVIDENCE_MAX_CHARS,
    GitAuthor,
    GitEvidenceExportResult,
    GitHistoryIndexResult,
    export_git_evidence,
    list_git_authors,
    parse_git_history,
    read_git_commit_text,
    write_git_history_index,
)
from learntrace.parsers.git_file import write_git_file
from learntrace.parsers.git_navigation import DEFAULT_SOURCE_LINES, GitNavigationResult
from learntrace.parsers.git_tree import write_git_tree
from learntrace.parsers.git_worktree import write_git_worktree
from learntrace.parsers.serialization import write_parse_result
from learntrace.parsers.static import parse_static_materials
from learntrace.parsers.test_logs import parse_test_logs
from learntrace.parsers.types import (
    PARSER_VERSION,
    AnalysisScope,
    ParseResult,
    ParseWarning,
    ProjectInventory,
)

__all__ = [
    "DiscoveredMaterials",
    "DEFAULT_EVIDENCE_MAX_CHARS",
    "GitEvidenceExportResult",
    "GitHistoryIndexResult",
    "GitNavigationResult",
    "DEFAULT_SOURCE_LINES",
    "PARSER_VERSION",
    "AnalysisScope",
    "GitAuthor",
    "ParseResult",
    "ParseWarning",
    "ProjectInventory",
    "discover_static_materials",
    "export_git_evidence",
    "list_git_authors",
    "parse_documents",
    "parse_git_history",
    "parse_static_materials",
    "read_git_commit_text",
    "parse_test_logs",
    "write_parse_result",
    "write_git_history_index",
    "write_git_file",
    "write_git_tree",
    "write_git_worktree",
]
