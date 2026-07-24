"""Deterministic Task 4 pipeline for candidate inference and confirmation handling."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from learntrace import __version__
from learntrace.models import (
    SCHEMA_VERSION,
    CandidateStatus,
    ContractValidator,
    EventKind,
    LearningNodeCandidate,
    MissingInfo,
    NodeType,
    ObservableEvent,
    StudentConfirmation,
    TextOrMissing,
)

ARCHIVE_VERSION = "v0"
HASH_ALGORITHM = "sha256"


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    node_type: NodeType
    statement: str
    basis_event_ids: tuple[str, ...]
    uncertainty: str
    question_to_student: TextOrMissing


@dataclass(frozen=True, slots=True)
class ArchiveWarning:
    code: str
    source: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "source": self.source, "message": self.message}


@dataclass(frozen=True, slots=True)
class ArchiveBundle:
    events: tuple[ObservableEvent, ...]
    candidates: tuple[LearningNodeCandidate, ...]
    confirmations: tuple[StudentConfirmation, ...]
    warnings: tuple[ArchiveWarning, ...] = ()


@dataclass(slots=True)
class SourceIndexEntry:
    source_ref: dict[str, Any]
    event_ids: list[str]
    candidate_ids: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_ref": self.source_ref,
            "event_ids": self.event_ids,
            "candidate_ids": self.candidate_ids,
        }


class CandidateInferencer(Protocol):
    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]: ...


_SCENARIO_ID_PATTERN = re.compile(r"^evt-([^-]+)-")


class StubCandidateInferencer:
    """Default stub inferencer.

    The logic is deterministic and pluggable so a real LLM-backed adapter can be
    substituted later without changing the Task 4 pipeline.
    """

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        ordered = tuple(sorted(events, key=_event_sort_key))
        traces = tuple(event for event in ordered if event.kind == EventKind.TRACE_RECORD)
        commits = tuple(event for event in ordered if event.kind == EventKind.GIT_COMMIT)
        test_logs = tuple(event for event in ordered if event.kind == EventKind.TEST_LOG)
        documents = tuple(event for event in ordered if event.kind == EventKind.DOCUMENT)

        if len(traces) >= 2 and not commits and not documents:
            return (self._follow_up_candidate(traces[0], traces[1]),)
        if traces and commits:
            return (self._revise_ai_candidate(traces[0], commits[-1]),)
        if commits and _has_failure_log(test_logs):
            return (self._fix_failed_candidate(test_logs[0], commits[-1]),)
        if commits and test_logs and _looks_like_test_addition(commits[-1]):
            return (self._add_tests_candidate(commits[-1], test_logs[-1]),)
        if documents and commits:
            return (self._adjust_constraints_candidate(documents[-1], commits[-1]),)
        if commits and _looks_like_rewrite(commits[-1]):
            return (self._commit_only_fix_candidate(commits[-1]),)
        if commits and _looks_like_constraint_change(commits[-1]):
            return (self._commit_only_constraint_candidate(commits[-1]),)
        return ()

    def _revise_ai_candidate(
        self,
        trace: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:
        if "默认类型推断" in trace.summary and "显式指定 dtype" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.REVISE_AI_SUGGESTION,
                statement=(
                    "学生可能判断 AI 建议的默认类型推断不适合含千分位的数据，改为显式 dtype 方案。"
                ),
                basis_event_ids=(trace.id, commit.id),
                uncertainty=(
                    "中：AI 建议与代码提交是两条独立记录，系统不预设二者相关；"
                    "是否参考及修改动机均需学生确认。"
                ),
                question_to_student="你是否因为默认类型推断无法处理千分位而修改了 AI 的建议？",
            )
        if "正则表达式" in trace.summary and "isdigit" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.REVISE_AI_SUGGESTION,
                statement="学生可能将 AI 建议的正则校验改写为更简单的字符串方法实现。",
                basis_event_ids=(trace.id, commit.id),
                uncertainty=(
                    "中：AI 建议与代码提交是两条独立记录，系统不预设二者相关；"
                    "两种实现语义相近，是否参考需学生确认。"
                ),
                question_to_student="这次实现是否参考并修改了 AI 提出的正则校验建议？",
            )
        return CandidateDraft(
            node_type=NodeType.REVISE_AI_SUGGESTION,
            statement="学生可能调整了 AI 给出的实现建议，并采用了不同的落地方案。",
            basis_event_ids=(trace.id, commit.id),
            uncertainty=("中：轨迹建议与代码结果相邻，但系统不能把二者直接当作同一决策链。"),
            question_to_student="这次实现是否参考并修改了 AI 给出的建议？",
        )

    def _fix_failed_candidate(
        self,
        test_log: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:
        if "ZeroDivisionError" in test_log.summary and "返回 None" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能在除零测试失败后为 divide 增加了零值保护。",
                basis_event_ids=(test_log.id, commit.id),
                uncertainty="低：失败用例与提交修改点直接对应，时间顺序吻合。",
                question_to_student="这次提交是否是为了修复日志中的除零失败？",
            )
        return CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能在失败日志出现后调整了实现，修复了先前的方法。",
            basis_event_ids=(test_log.id, commit.id),
            uncertainty="低：失败日志与后续提交存在直接的时间和内容关联。",
            question_to_student="这次修改是否是为了修复日志里的失败？",
        )

    def _add_tests_candidate(
        self,
        commit: ObservableEvent,
        test_log: ObservableEvent,
    ) -> CandidateDraft:
        if "test_parser_edge.py" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.ADD_TESTS,
                statement="学生可能在实现解析功能后主动补充了边界用例测试。",
                basis_event_ids=(commit.id, test_log.id),
                uncertainty="低：提交内容即为新测试文件且全部通过，意图明确。",
                question_to_student="这些边界用例是你自己识别并补充的吗？",
            )
        return CandidateDraft(
            node_type=NodeType.ADD_TESTS,
            statement="学生可能在实现功能后补充了新的测试用例。",
            basis_event_ids=(commit.id, test_log.id),
            uncertainty="低：测试新增与通过日志直接对应。",
            question_to_student="这些新增测试是否由你主动识别并补充？",
        )

    def _follow_up_candidate(
        self,
        first_trace: ObservableEvent,
        second_trace: ObservableEvent,
    ) -> CandidateDraft:
        if "成绩分布" in first_trace.summary and "缺失" in second_trace.summary:
            return CandidateDraft(
                node_type=NodeType.FOLLOW_UP,
                statement="学生可能通过追问把问题从基础统计细化到含缺失值的统计。",
                basis_event_ids=(first_trace.id, second_trace.id),
                uncertainty="中：追问内容相关，但是否形成新的理解只有学生能确认。",
                question_to_student="第二次追问是否让你对缺失值处理有了新的理解？",
            )
        return CandidateDraft(
            node_type=NodeType.FOLLOW_UP,
            statement="学生可能通过连续追问把问题进一步细化。",
            basis_event_ids=(first_trace.id, second_trace.id),
            uncertainty="中：追问主题连续，但新的学习收获仍需学生确认。",
            question_to_student="后续追问是否让你形成了新的理解？",
        )

    def _adjust_constraints_candidate(
        self,
        document: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:
        if "等级制" in document.summary and "等级制" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能因设计约束从百分制改为等级制而重写了统计逻辑。",
                basis_event_ids=(document.id, commit.id),
                uncertainty="中：文档与提交时间相邻，但文档更新者身份未记录，无法确认是学生本人调整。",
                question_to_student="等级制这个约束调整是你自己提出的，还是课程要求变更？",
            )
        return CandidateDraft(
            node_type=NodeType.ADJUST_CONSTRAINTS,
            statement="学生可能因为设计约束变化而调整了实现。",
            basis_event_ids=(document.id, commit.id),
            uncertainty="中：文档变化与代码调整相邻，但约束来源仍需学生说明。",
            question_to_student="这次约束调整是你主动提出的，还是来自外部要求？",
        )

    def _commit_only_fix_candidate(self, commit: ObservableEvent) -> CandidateDraft:
        if "逐字符读取" in commit.summary or "解析循环" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能放弃了逐字符读取的初始方案，改为整行解析。",
                basis_event_ids=(commit.id,),
                uncertainty="高：无任何测试日志或轨迹记录初始方案失败，仅有单次提交，失败假设无法核实。",
                question_to_student="重写解析循环之前，初始方案是否遇到过失败？",
            )
        return CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能放弃了之前的方案并改写为新的实现。",
            basis_event_ids=(commit.id,),
            uncertainty="高：仅有一次提交，缺少失败日志或轨迹来解释改写原因。",
            question_to_student="这次大幅改写之前，原方案是否遇到过问题？",
        )

    def _commit_only_constraint_candidate(self, commit: ObservableEvent) -> CandidateDraft:
        if "--ignore-missing" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能放宽了输入约束，允许缺失的数据文件被跳过。",
                basis_event_ids=(commit.id,),
                uncertainty="高：仅有单次提交，无测试日志、无轨迹、无文档，约束调整的原因与过程均不可知。",
                question_to_student="增加 --ignore-missing 是出于什么考虑？",
            )
        return CandidateDraft(
            node_type=NodeType.ADJUST_CONSTRAINTS,
            statement="学生可能调整了输入或运行约束。",
            basis_event_ids=(commit.id,),
            uncertainty="高：只有提交记录，缺少文档、日志或轨迹来说明约束变化。",
            question_to_student="这次约束调整背后的考虑是什么？",
        )


def _event_sort_key(event: ObservableEvent) -> tuple[str, str]:
    return (event.occurred_at or "", event.id)


def _has_failure_log(test_logs: tuple[ObservableEvent, ...]) -> bool:
    return any("失败" in log.summary or "error" in log.summary.lower() for log in test_logs)


def _looks_like_test_addition(commit: ObservableEvent) -> bool:
    lowered = commit.summary.lower()
    return "test" in lowered and (
        "新增" in commit.summary or "cover" in lowered or "覆盖" in commit.summary
    )


def _looks_like_rewrite(commit: ObservableEvent) -> bool:
    return any(term in commit.summary for term in ("重写", "去掉", "改写"))


def _looks_like_constraint_change(commit: ObservableEvent) -> bool:
    lowered = commit.summary.lower()
    return "--ignore-missing" in lowered or "约束" in commit.summary or "边界" in commit.summary


def _dedupe_events(events: tuple[ObservableEvent, ...]) -> tuple[ObservableEvent, ...]:
    by_id: dict[str, ObservableEvent] = {}
    for event in sorted(events, key=_event_sort_key):
        existing = by_id.get(event.id)
        if existing is None:
            by_id[event.id] = event
            continue
        if existing.to_dict() != event.to_dict():
            msg = f"conflicting observable_event records for id {event.id}"
            raise ValueError(msg)
    return tuple(by_id.values())


def _dedupe_confirmations(
    confirmations: tuple[StudentConfirmation, ...],
) -> tuple[StudentConfirmation, ...]:
    by_id: dict[str, StudentConfirmation] = {}
    ordered = sorted(confirmations, key=lambda item: (item.confirmed_at or "", item.id))
    for confirmation in ordered:
        existing = by_id.get(confirmation.id)
        if existing is None:
            by_id[confirmation.id] = confirmation
            continue
        if existing.to_dict() != confirmation.to_dict():
            msg = f"conflicting student_confirmation records for id {confirmation.id}"
            raise ValueError(msg)
    return tuple(by_id.values())


def _dedupe_warnings(warnings: tuple[ArchiveWarning, ...]) -> tuple[ArchiveWarning, ...]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[ArchiveWarning] = []
    for warning in warnings:
        key = (warning.code, warning.source, warning.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(warning)
    return tuple(unique)


def _candidate_id_for(draft: CandidateDraft, index: int) -> str:
    for event_id in draft.basis_event_ids:
        match = _SCENARIO_ID_PATTERN.match(event_id)
        if match is not None:
            return f"cand-{match.group(1)}"
    return f"cand-{draft.node_type.value}-{index}"


def _materialize_candidates(
    drafts: tuple[CandidateDraft, ...],
    confirmations: tuple[StudentConfirmation, ...],
) -> tuple[LearningNodeCandidate, ...]:
    confirmation_ids = {confirmation.candidate_id for confirmation in confirmations}
    materialized: list[LearningNodeCandidate] = []
    used_ids: set[str] = set()
    for index, draft in enumerate(drafts, start=1):
        candidate_id = _candidate_id_for(draft, index)
        if candidate_id in used_ids:
            candidate_id = f"{candidate_id}-{index}"
        used_ids.add(candidate_id)
        status = (
            CandidateStatus.RESOLVED
            if candidate_id in confirmation_ids
            else CandidateStatus.PROPOSED
        )
        materialized.append(
            LearningNodeCandidate(
                id=candidate_id,
                node_type=draft.node_type,
                statement=draft.statement,
                basis_event_ids=draft.basis_event_ids,
                uncertainty=draft.uncertainty,
                question_to_student=draft.question_to_student,
                status=status,
            )
        )
    return tuple(materialized)


def validate_bundle(
    bundle: ArchiveBundle,
    *,
    validator: ContractValidator | None = None,
) -> None:
    contract_validator = validator if validator is not None else ContractValidator()
    for event in bundle.events:
        contract_validator.validate("observable_event", event.to_dict())
    for candidate in bundle.candidates:
        contract_validator.validate("learning_node_candidate", candidate.to_dict())
    for confirmation in bundle.confirmations:
        contract_validator.validate("student_confirmation", confirmation.to_dict())

    all_ids = [record.id for record in (*bundle.events, *bundle.candidates, *bundle.confirmations)]
    duplicate_ids = sorted({record_id for record_id in all_ids if all_ids.count(record_id) > 1})
    if duplicate_ids:
        msg = f"duplicate record ids: {', '.join(duplicate_ids)}"
        raise ValueError(msg)

    event_ids = {event.id for event in bundle.events}
    candidate_ids = {candidate.id for candidate in bundle.candidates}
    answered_ids = {confirmation.candidate_id for confirmation in bundle.confirmations}

    violations: list[str] = []
    for candidate in bundle.candidates:
        for basis_id in candidate.basis_event_ids:
            if basis_id not in event_ids:
                violations.append(f"{candidate.id}: missing basis event {basis_id}")
        if candidate.status == CandidateStatus.RESOLVED and candidate.id not in answered_ids:
            violations.append(f"{candidate.id}: resolved candidate has no confirmation")
        if candidate.status == CandidateStatus.PROPOSED and candidate.id in answered_ids:
            violations.append(f"{candidate.id}: proposed candidate already has confirmation")
    for confirmation in bundle.confirmations:
        if confirmation.candidate_id not in candidate_ids:
            violations.append(f"{confirmation.id}: confirmation targets missing candidate")

    if violations:
        raise ValueError("; ".join(violations))


def build_archive_bundle(
    events: tuple[ObservableEvent, ...],
    *,
    confirmations: tuple[StudentConfirmation, ...] = (),
    warnings: tuple[ArchiveWarning, ...] = (),
    validator: ContractValidator | None = None,
    inferencer: CandidateInferencer | None = None,
) -> ArchiveBundle:
    deduped_events = _dedupe_events(events)
    sorted_confirmations = _dedupe_confirmations(confirmations)
    candidate_inferencer = inferencer if inferencer is not None else StubCandidateInferencer()
    drafts = candidate_inferencer.infer(deduped_events)
    candidates = _materialize_candidates(drafts, sorted_confirmations)
    bundle = ArchiveBundle(
        events=deduped_events,
        candidates=candidates,
        confirmations=sorted_confirmations,
        warnings=_dedupe_warnings(warnings),
    )
    validate_bundle(bundle, validator=validator)
    return bundle


def _text_or_missing_to_json(value: TextOrMissing) -> str | dict[str, Any]:
    if isinstance(value, MissingInfo):
        return value.to_dict()
    return value


def _missing_info_count(bundle: ArchiveBundle) -> int:
    candidate_missing = sum(
        isinstance(candidate.question_to_student, MissingInfo) for candidate in bundle.candidates
    )
    confirmation_missing = sum(
        isinstance(confirmation.student_statement, MissingInfo)
        for confirmation in bundle.confirmations
    )
    return candidate_missing + confirmation_missing


def _source_index(bundle: ArchiveBundle) -> list[dict[str, object]]:
    candidate_ids_by_event = {
        event.id: [
            candidate.id for candidate in bundle.candidates if event.id in candidate.basis_event_ids
        ]
        for event in bundle.events
    }
    index: dict[str, SourceIndexEntry] = {}
    for event in bundle.events:
        for ref in event.source_refs:
            key = f"{ref.type.value}:{ref.ref}"
            entry = index.setdefault(
                key,
                SourceIndexEntry(source_ref=ref.to_dict(), event_ids=[], candidate_ids=[]),
            )
            if event.id not in entry.event_ids:
                entry.event_ids.append(event.id)
            for candidate_id in candidate_ids_by_event[event.id]:
                if candidate_id not in entry.candidate_ids:
                    entry.candidate_ids.append(candidate_id)
    return [entry.to_dict() for entry in index.values()]


def _candidate_links(bundle: ArchiveBundle) -> list[dict[str, object]]:
    confirmation_by_candidate = {
        confirmation.candidate_id: confirmation.id for confirmation in bundle.confirmations
    }
    return [
        {
            "candidate_id": candidate.id,
            "node_type": candidate.node_type.value,
            "basis_event_ids": list(candidate.basis_event_ids),
            "confirmation_id": confirmation_by_candidate.get(candidate.id),
            "status": candidate.status.value,
        }
        for candidate in bundle.candidates
    ]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def archive_manifest(bundle: ArchiveBundle) -> dict[str, object]:
    """Return deterministic metadata for reproducing and auditing an archive."""

    record_sets = {
        "observable_fact": [event.to_dict() for event in bundle.events],
        "candidate_inference": [candidate.to_dict() for candidate in bundle.candidates],
        "student_confirmation": [confirmation.to_dict() for confirmation in bundle.confirmations],
        "warnings": [warning.to_dict() for warning in bundle.warnings],
    }
    return {
        "tool": "learntrace",
        "tool_version": __version__,
        "archive_version": ARCHIVE_VERSION,
        "schema_version": SCHEMA_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "content_fingerprint": _sha256(record_sets),
        "record_hashes": {
            record_type: _sha256(records) for record_type, records in record_sets.items()
        },
    }


def bundle_to_dict(
    bundle: ArchiveBundle,
    *,
    validator: ContractValidator | None = None,
) -> dict[str, object]:
    validate_bundle(bundle, validator=validator)
    pending_questions = [
        {
            "candidate_id": candidate.id,
            "node_type": candidate.node_type.value,
            "question_to_student": _text_or_missing_to_json(candidate.question_to_student),
            "basis_event_ids": list(candidate.basis_event_ids),
            "uncertainty": candidate.uncertainty,
        }
        for candidate in bundle.candidates
        if candidate.status == CandidateStatus.PROPOSED
    ]
    missing_info_count = _missing_info_count(bundle)
    return {
        "archive_version": ARCHIVE_VERSION,
        "archive_manifest": archive_manifest(bundle),
        "record_counts": {
            "observable_fact": len(bundle.events),
            "candidate_inference": len(bundle.candidates),
            "student_confirmation": len(bundle.confirmations),
            "missing_info": missing_info_count,
            "warnings": len(bundle.warnings),
            "pending_questions": len(pending_questions),
        },
        "quality_checks": {
            "schema_valid": True,
            "basis_events_resolved": True,
            "resolved_candidates_have_confirmation": True,
            "no_duplicate_record_ids": True,
        },
        "risk_flags": {
            "has_warnings": bool(bundle.warnings),
            "has_pending_questions": bool(pending_questions),
            "has_missing_info": missing_info_count > 0,
        },
        "events": [event.to_dict() for event in bundle.events],
        "candidates": [candidate.to_dict() for candidate in bundle.candidates],
        "confirmations": [confirmation.to_dict() for confirmation in bundle.confirmations],
        "warnings": [warning.to_dict() for warning in bundle.warnings],
        "pending_questions": pending_questions,
        "source_index": _source_index(bundle),
        "candidate_links": _candidate_links(bundle),
    }
