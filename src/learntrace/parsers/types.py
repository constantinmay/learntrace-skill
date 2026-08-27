"""静态解析任务线的公共结果类型。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from learntrace.models import ContractValidator, ObservableEvent

PARSER_VERSION = "v0"


@dataclass(frozen=True, slots=True)
class AnalysisScope:
    """用户确认后实际进入解析的仓库相对范围。"""

    include_git: bool
    documents: tuple[str, ...]
    test_logs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "include_git": self.include_git,
            "documents": list(self.documents),
            "test_logs": list(self.test_logs),
        }


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    """材料发现阶段生成的确定性项目文件清单。"""

    git_available: bool
    tracked_files: tuple[str, ...]
    metadata_only_files: tuple[str, ...]
    files: tuple[str, ...]
    source_files: tuple[str, ...]
    test_files: tuple[str, ...]
    documents: tuple[str, ...]
    test_logs: tuple[str, ...]
    task_documents: tuple[str, ...]
    report_documents: tuple[str, ...]
    design_documents: tuple[str, ...]
    extension_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "git_available": self.git_available,
            "tracked_files": list(self.tracked_files),
            "metadata_only_files": list(self.metadata_only_files),
            "files": list(self.files),
            "source_files": list(self.source_files),
            "test_files": list(self.test_files),
            "documents": list(self.documents),
            "test_logs": list(self.test_logs),
            "task_documents": list(self.task_documents),
            "report_documents": list(self.report_documents),
            "design_documents": list(self.design_documents),
            "extension_counts": dict(self.extension_counts),
        }


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """一项未阻断整批解析的明确告警。"""

    code: str
    source: str
    message: str
    details: tuple[tuple[str, int | str | bool], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "source": self.source,
            "message": self.message,
        }
        if self.details:
            data["details"] = dict(self.details)
        return data


@dataclass(frozen=True, slots=True)
class ParseResult:
    """解析出的事实事件及可定位告警。"""

    events: tuple[ObservableEvent, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()
    scope: AnalysisScope | None = None
    inventory: ProjectInventory | None = None

    def merged(self, *others: ParseResult) -> ParseResult:
        events = list(self.events)
        warnings = list(self.warnings)
        scope = self.scope
        inventory = self.inventory
        for other in others:
            events.extend(other.events)
            warnings.extend(other.warnings)
            if scope is None:
                scope = other.scope
            if inventory is None:
                inventory = other.inventory
        return ParseResult(
            events=tuple(events),
            warnings=tuple(warnings),
            scope=scope,
            inventory=inventory,
        )

    def to_dict(self, validator: ContractValidator | None = None) -> dict[str, Any]:
        """序列化批量结果，并在输出前校验事件及全局 id。"""
        contract = validator if validator is not None else ContractValidator()
        event_ids = [event.id for event in self.events]
        duplicate_ids = sorted(
            event_id for event_id, count in Counter(event_ids).items() if count > 1
        )
        if duplicate_ids:
            msg = f"duplicate observable event ids: {', '.join(duplicate_ids)}"
            raise ValueError(msg)
        serialized_events = [event.to_dict() for event in self.events]
        for event in serialized_events:
            contract.validate("observable_event", event)
        data: dict[str, Any] = {
            "parser_version": PARSER_VERSION,
            "events": serialized_events,
            "warnings": [warning.to_dict() for warning in self.warnings],
        }
        if self.scope is not None:
            data["analysis_scope"] = self.scope.to_dict()
        if self.inventory is not None:
            data["inventory"] = self.inventory.to_dict()
        return data
