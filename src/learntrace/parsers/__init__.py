"""只读的本地项目静态材料解析器。"""

from learntrace.parsers.discovery import DiscoveredMaterials, discover_static_materials
from learntrace.parsers.documents import parse_documents
from learntrace.parsers.git import (
    GitAuthor,
    list_git_authors,
    parse_git_history,
    read_git_commit_text,
)
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
    "PARSER_VERSION",
    "AnalysisScope",
    "GitAuthor",
    "ParseResult",
    "ParseWarning",
    "ProjectInventory",
    "discover_static_materials",
    "list_git_authors",
    "parse_documents",
    "parse_git_history",
    "parse_static_materials",
    "read_git_commit_text",
    "parse_test_logs",
    "write_parse_result",
]
