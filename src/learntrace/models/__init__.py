"""由版本化 Schema 派生的共享数据模型。"""

from learntrace.models.records import (
    SCHEMA_VERSION,
    CandidateStatus,
    ConfirmationDecision,
    EventKind,
    LearningNodeCandidate,
    MissingInfo,
    ObservableEvent,
    SourceRef,
    SourceType,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.models.validation import ContractValidator, RecordType, default_schema_dir

__all__ = [
    "SCHEMA_VERSION",
    "CandidateStatus",
    "ConfirmationDecision",
    "ContractValidator",
    "EventKind",
    "LearningNodeCandidate",
    "MissingInfo",
    "ObservableEvent",
    "RecordType",
    "SourceRef",
    "SourceType",
    "StudentConfirmation",
    "TextOrMissing",
    "default_schema_dir",
]
