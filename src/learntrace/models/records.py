"""与 ``schemas/v0/`` 中冻结 Schema 一一对应的记录模型。

模型只提供结构化构造与固定字段注入（schema_version / evidence_level），
不在构造时做运行时校验。任何外部输入以及需要落盘或跨模块传递的输出，
仍必须经过 ``ContractValidator`` 校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Literal

SCHEMA_VERSION: str = "v0"


class SourceType(StrEnum):
    """记录可引用的证据来源类型。"""

    GIT_COMMIT = "git_commit"
    FILE = "file"
    DOCUMENT = "document"
    TEST_LOG = "test_log"
    TRACE_RECORD = "trace_record"


@dataclass(frozen=True, slots=True)
class SourceRef:
    """一条可追溯的证据来源指针。"""

    type: SourceType
    ref: str
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": str(self.type), "ref": self.ref}
        if self.note is not None:
            data["note"] = self.note
        return data


@dataclass(frozen=True, slots=True)
class MissingInfo:
    """显式的证据缺失状态，展示为"未记录"。

    禁止用普通字符串表示缺失证据；必须使用此结构化形式，
    以便下游消费者区分"无证据"与"已记录的内容"。
    """

    note: str | None = None
    status: Literal["not_recorded"] = field(default="not_recorded", init=False)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status}
        if self.note is not None:
            data["note"] = self.note
        return data


TextOrMissing = str | MissingInfo


def _text_or_missing_to_dict(value: TextOrMissing) -> str | dict[str, Any]:
    if isinstance(value, MissingInfo):
        return value.to_dict()
    return value


class EventKind(StrEnum):
    """可观察事件的类型。"""

    GIT_COMMIT = "git_commit"
    DOCUMENT = "document"
    TEST_LOG = "test_log"
    TRACE_RECORD = "trace_record"


@dataclass(frozen=True, slots=True)
class ObservableEvent:
    """可直接追溯到至少一条来源引用的事实。"""

    EVIDENCE_LEVEL: ClassVar[str] = "observable_fact"

    id: str
    kind: EventKind
    summary: str
    source_refs: tuple[SourceRef, ...]
    occurred_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "evidence_level": self.EVIDENCE_LEVEL,
            "kind": str(self.kind),
            "summary": self.summary,
            "source_refs": [ref.to_dict() for ref in self.source_refs],
        }
        if self.occurred_at is not None:
            data["occurred_at"] = self.occurred_at
        return data


class NodeType(StrEnum):
    """候选学习节点的分类；档案层按此筛选与统计。新增类型属于契约变更。"""

    FOLLOW_UP = "follow_up"
    REVISE_AI_SUGGESTION = "revise_ai_suggestion"
    FIX_FAILED_APPROACH = "fix_failed_approach"
    ADD_TESTS = "add_tests"
    ADJUST_CONSTRAINTS = "adjust_constraints"


class CandidateStatus(StrEnum):
    """候选的生命周期。学生的决定只存放在 StudentConfirmation 中。"""

    PROPOSED = "proposed"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class LearningNodeCandidate:
    """系统提出的候选学习节点，未经确认本身不构成结论。"""

    EVIDENCE_LEVEL: ClassVar[str] = "candidate_inference"

    id: str
    node_type: NodeType
    statement: str
    basis_event_ids: tuple[str, ...]
    uncertainty: str
    question_to_student: TextOrMissing
    status: CandidateStatus = CandidateStatus.PROPOSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "evidence_level": self.EVIDENCE_LEVEL,
            "node_type": str(self.node_type),
            "statement": self.statement,
            "basis_event_ids": list(self.basis_event_ids),
            "uncertainty": self.uncertainty,
            "question_to_student": _text_or_missing_to_dict(self.question_to_student),
            "status": str(self.status),
        }


class ConfirmationDecision(StrEnum):
    """学生对候选的回答。"""

    CONFIRMED = "confirmed"
    SUPPLEMENTED = "supplemented"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class StudentConfirmation:
    """学生的回答，与原始候选分开独立保存。"""

    EVIDENCE_LEVEL: ClassVar[str] = "student_confirmation"

    id: str
    candidate_id: str
    decision: ConfirmationDecision
    student_statement: TextOrMissing
    confirmed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "id": self.id,
            "evidence_level": self.EVIDENCE_LEVEL,
            "candidate_id": self.candidate_id,
            "decision": str(self.decision),
            "student_statement": _text_or_missing_to_dict(self.student_statement),
        }
        if self.confirmed_at is not None:
            data["confirmed_at"] = self.confirmed_at
        return data
