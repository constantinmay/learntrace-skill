"""Deterministic Task 4 pipeline for candidate inference and confirmation handling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from learntrace import __version__
from learntrace.models import (
    SCHEMA_VERSION,
    CandidateStatus,
    ConfirmationDecision,
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
MAX_CANDIDATES = 50
_MAX_TRACE_COMMIT_GAP_SECONDS = 30 * 60

_CONSTRAINT_TERMS = (
    "等级制",
    "百分制",
    "必填",
    "可选",
    "默认",
    "上限",
    "下限",
    "边界",
    "约束",
    "限制",
    "格式",
    "评分",
    "状态",
)
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{3,}")
_LOW_SIGNAL_TRACE_TERMS = (
    "命令类型：git add",
    "命令类型：git status",
    "命令类型：kill",
    "命令类型：pgrep",
    "命令类型：pkill",
    "命令类型：ss",
    "工具 todowrite",
)
_UNCERTAINTY_PRIORITY = {"低：": 0, "中：": 1, "高：": 2}


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

    inference_mode: str = "stub"

    task2_meta: dict[str, Any] = field(default_factory=dict[str, Any])


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

_COMMIT_OVERVIEW_PATTERN = re.compile(
    r"^(?:提交|commit)\s+[a-f0-9]+\s*(?:[：:]|的提交信息为)",
    re.IGNORECASE,
)


def _is_commit_overview(summary: str) -> bool:
    """Return True if *summary* is a commit overview event (not a file-level change)."""

    return _COMMIT_OVERVIEW_PATTERN.match(summary) is not None


class StubCandidateInferencer:
    """Default stub inferencer.





    The logic is deterministic and pluggable so a real LLM-backed adapter can be


    substituted later without changing the Task 4 pipeline.


    """

    inference_mode = "stub"

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:

        ordered = tuple(sorted(events, key=_event_sort_key))

        traces = tuple(event for event in ordered if event.kind == EventKind.TRACE_RECORD)

        # Only commit overview events (not file-level changes) are used for

        # candidate inference. A commit overview summary starts with

        # "提交 <hash>：" (e.g. "提交 d9e0f1a：调整参数解析").

        commits = tuple(
            event
            for event in ordered
            if event.kind == EventKind.GIT_COMMIT and _is_commit_overview(event.summary)
        )

        test_logs = tuple(event for event in ordered if event.kind == EventKind.TEST_LOG)

        documents = tuple(event for event in ordered if event.kind == EventKind.DOCUMENT)

        failing_logs = tuple(log for log in test_logs if _is_failure(log.summary))

        drafts: list[CandidateDraft] = []

        # Candidate-affecting ordering: a commit may not be presented as
        # *following* another event when the timestamps prove the reverse
        # (REJECTED); CONFIRMED and UNVERIFIABLE (missing timestamps) both
        # remain acceptable, the latter with conservative copy.
        def precedes(earlier: ObservableEvent, later: ObservableEvent) -> bool:
            return _temporally_plausible(earlier, later) != TemporalPlausibility.REJECTED

        # Follow-up: consecutive authorizations that ask deepening questions.
        # Keep the original no-anchor behavior for semantic trace-only cases.
        if len(traces) >= 2 and not commits and not documents:
            drafts.append(self._follow_up_candidate(traces[0], traces[1]))

        # Revising AI suggestions: an authorized trace followed by a commit.
        # Bind only to the matching summary pair, and only when the trace
        # does not provably succeed the commit.
        for trace in traces:
            for commit in commits:
                if self._revise_ai_matches(trace, commit) and precedes(trace, commit):
                    drafts.append(self._revise_ai_candidate(trace, commit))

        # Minimal Task 3 traces often record only a completed tool operation.
        # Associate those conservatively with the nearest following commit,
        # one-to-one and within a bounded time window. This preserves trace
        # provenance without claiming that a particular AI suggestion was used.
        drafts.extend(self._trace_commit_follow_ups(traces, commits, drafts))

        # Fixes: bind the FAILING log (not an arbitrary first test log) to the
        # commit that follows it.
        for failing_log in failing_logs:
            for commit in commits:
                if not precedes(failing_log, commit):
                    continue
                drafts.append(self._fix_failed_candidate(failing_log, commit))

        # Added tests: a test-addition commit followed by a test log.
        for commit in commits:
            if not _looks_like_test_addition(commit):
                continue
            for test_log in test_logs:
                if precedes(commit, test_log):
                    drafts.append(self._add_tests_candidate(commit, test_log))

        # Constraint/design changes: retain only the strongest topical document
        # match for each commit. Temporal plausibility alone is insufficient:
        # documents without timestamps otherwise create a Cartesian product.
        for commit in commits:
            matches = (
                (self._adjust_constraints_match_score(document, commit), document)
                for document in documents
                if precedes(document, commit)
            )
            best_score, best_document = max(
                matches,
                key=lambda item: (item[0], item[1].id),
                default=(0, None),
            )
            if best_score > 0 and best_document is not None:
                drafts.append(self._adjust_constraints_candidate(best_document, commit))

        # Commit-only fallbacks: emit at most one, only when no higher-certainty
        # candidate already covered this commit.
        covered_commits = {bid for d in drafts for bid in d.basis_event_ids}
        for commit in commits:
            if commit.id in covered_commits:
                continue
            if _looks_like_rewrite(commit):
                drafts.append(self._commit_only_fix_candidate(commit))
            elif _looks_like_constraint_change(commit):
                drafts.append(self._commit_only_constraint_candidate(commit))

        return tuple(drafts)

    def _trace_commit_follow_ups(
        self,
        traces: tuple[ObservableEvent, ...],
        commits: tuple[ObservableEvent, ...],
        existing_drafts: Iterable[CandidateDraft],
    ) -> tuple[CandidateDraft, ...]:
        used_event_ids = {
            event_id
            for draft in existing_drafts
            if draft.node_type == NodeType.REVISE_AI_SUGGESTION
            for event_id in draft.basis_event_ids
        }
        available = [
            trace
            for trace in traces
            if trace.id not in used_event_ids and not _is_low_signal_trace(trace)
        ]
        follow_ups: list[CandidateDraft] = []
        for commit in commits:
            if commit.id in used_event_ids:
                continue
            preceding: list[tuple[float, ObservableEvent]] = []
            for trace in available:
                gap_seconds = _trace_commit_gap_seconds(trace, commit)
                if gap_seconds is not None and gap_seconds <= _MAX_TRACE_COMMIT_GAP_SECONDS:
                    preceding.append((gap_seconds, trace))
            if not preceding:
                continue
            _, trace = min(
                preceding,
                key=lambda item: (item[0], item[1].id),
            )
            available.remove(trace)
            follow_ups.append(self._trace_commit_follow_up_candidate(trace, commit))
        return tuple(follow_ups)

    @staticmethod
    def _adjust_constraints_match_score(
        document: ObservableEvent,
        commit: ObservableEvent,
    ) -> int:
        document_terms = _constraint_terms(document.summary)
        commit_terms = _constraint_terms(commit.summary)
        shared_terms = document_terms & commit_terms
        if shared_terms:
            return len(shared_terms) * 10

        shared_identifiers = _specific_identifiers(document.summary) & _specific_identifiers(
            commit.summary
        )
        if shared_identifiers and (document_terms or commit_terms):
            return len(shared_identifiers)
        return 0

    def _revise_ai_matches(self, trace: ObservableEvent, commit: ObservableEvent) -> bool:
        """Whether a trace/commit pair is a topical AI-suggestion revision.

        The temporal ordering is applied by the caller (``precedes``); here we
        only check that the summaries actually correspond, so unrelated nearby
        commits are not turned into a revision claim.
        """
        return ("默认类型推断" in trace.summary and "显式指定 dtype" in commit.summary) or (
            "正则表达式" in trace.summary and "isdigit" in commit.summary
        )

    def _revise_ai_candidate(
        self,
        trace: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:

        plausible = _plausibility_is_plausible(_temporally_plausible(trace, commit))

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
                    if plausible
                    else "高：时间顺序无法验证，AI 建议与代码修改的关联性存疑。"
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
                    if plausible
                    else "高：时间顺序无法验证，语义近似的修改可能来自其他原因。"
                ),
                question_to_student="这次实现是否参考并修改了 AI 提出的正则校验建议？",
            )

        return CandidateDraft(
            node_type=NodeType.REVISE_AI_SUGGESTION,
            statement="学生可能调整了 AI 给出的实现建议，并采用了不同的落地方案。",
            basis_event_ids=(trace.id, commit.id),
            uncertainty=(
                "中：轨迹建议与代码结果相邻，但系统不能把二者直接当作同一决策链。"
                if plausible
                else "高：时间顺序无法验证，轨迹与代码修改之间的因果关系不明。"
            ),
            question_to_student="这次实现是否参考并修改了 AI 给出的建议？",
        )

    def _fix_failed_candidate(
        self,
        test_log: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:

        plausible = _plausibility_is_plausible(_temporally_plausible(test_log, commit))

        if "ZeroDivisionError" in test_log.summary and "返回 None" in commit.summary:
            uncertainty = (
                "低：失败用例与提交修改点直接对应，时间顺序吻合。"
                if plausible
                else "中：内容相关但时间顺序无法验证。"
            )

            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能在除零测试失败后为 divide 增加了零值保护。",
                basis_event_ids=(test_log.id, commit.id),
                uncertainty=uncertainty,
                question_to_student="这次提交是否是为了修复日志中的除零失败？",
            )

        if plausible:
            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能在失败日志出现后调整了实现，修复了先前的方法。",
                basis_event_ids=(test_log.id, commit.id),
                uncertainty="低：失败日志与后续提交存在直接的时间和内容关联。",
                question_to_student="这次修改是否是为了修复日志里的失败？",
            )

        return CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能在失败日志出现后调整了实现。",
            basis_event_ids=(test_log.id, commit.id),
            uncertainty="中：内容相关但时间顺序无法验证，不确定失败是否先于修改。",
            question_to_student="这次修改是否与日志里的失败有关？",
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

        plausible = _plausibility_is_plausible(_temporally_plausible(first_trace, second_trace))

        if "成绩分布" in first_trace.summary and "缺失" in second_trace.summary:
            return CandidateDraft(
                node_type=NodeType.FOLLOW_UP,
                statement="学生可能通过追问把问题从基础统计细化到含缺失值的统计。",
                basis_event_ids=(first_trace.id, second_trace.id),
                uncertainty=(
                    "中：追问内容相关，但是否形成新的理解只有学生能确认。"
                    if plausible
                    else "高：两次追问时间顺序无法验证，内容相关性可能是偶然。"
                ),
                question_to_student="第二次追问是否让你对缺失值处理有了新的理解？",
            )

        return CandidateDraft(
            node_type=NodeType.FOLLOW_UP,
            statement="学生可能通过连续追问把问题进一步细化。",
            basis_event_ids=(first_trace.id, second_trace.id),
            uncertainty=(
                "中：追问主题连续，但新的学习收获仍需学生确认。"
                if plausible
                else "高：时间顺序无法验证，主题连续性可能是偶然。"
            ),
            question_to_student="后续追问是否让你形成了新的理解？",
        )

    @staticmethod
    def _trace_commit_follow_up_candidate(
        trace: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:
        return CandidateDraft(
            node_type=NodeType.FOLLOW_UP,
            statement="学生可能在使用编码助手完成一项工具操作后继续推进，并形成了后续代码提交。",
            basis_event_ids=(trace.id, commit.id),
            uncertainty=(
                "高：轨迹与提交仅在时间上邻近，工具操作的目的、提交内容与学习收获均需学生确认。"
            ),
            question_to_student="这次工具操作是否帮助你推进了后续提交？你从中形成了什么新理解？",
        )

    def _adjust_constraints_candidate(
        self,
        document: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:

        plausible = _plausibility_is_plausible(_temporally_plausible(document, commit))

        if "等级制" in document.summary and "等级制" in commit.summary:
            return CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能因设计约束从百分制改为等级制而重写了统计逻辑。",
                basis_event_ids=(document.id, commit.id),
                uncertainty=(
                    "中：文档与提交时间相邻，但文档更新者身份未记录，无法确认是学生本人调整。"
                    if plausible
                    else "高：文档与提交时间顺序无法验证，事件关联匹配可能是偶然。"
                ),
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


def _is_failure(summary: str) -> bool:

    return "失败" in summary or "error" in summary.lower()


class TemporalPlausibility(Enum):
    """Whether an event ordering can be established from timestamps.

    :attr CONFIRMED: both timestamps present and *before* is strictly earlier.
    :attr REJECTED: both timestamps present and *before* is NOT strictly earlier.
    :attr UNVERIFIABLE: at least one timestamp is missing or unparseable.
    """

    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNVERIFIABLE = "unverifiable"


def _parse_rfc3339(value: str) -> datetime | None:
    """Parse an RFC3339 timestamp into an aware UTC datetime.

    Normalizes offsets so that ``10:00+08:00`` compares equal to ``02:00Z``.
    Returns ``None`` when the value is missing, malformed, or naive.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        return None  # naive timestamp: cannot reason about ordering
    return parsed.astimezone(UTC)


def _trace_commit_gap_seconds(
    trace: ObservableEvent,
    commit: ObservableEvent,
) -> float | None:
    if trace.occurred_at is None or commit.occurred_at is None:
        return None
    trace_time = _parse_rfc3339(trace.occurred_at)
    commit_time = _parse_rfc3339(commit.occurred_at)
    if trace_time is None or commit_time is None or trace_time >= commit_time:
        return None
    return (commit_time - trace_time).total_seconds()


def _is_low_signal_trace(trace: ObservableEvent) -> bool:
    return any(term in trace.summary for term in _LOW_SIGNAL_TRACE_TERMS)


def _constraint_terms(summary: str) -> frozenset[str]:
    return frozenset(term for term in _CONSTRAINT_TERMS if term in summary)


def _specific_identifiers(summary: str) -> frozenset[str]:
    return frozenset(
        identifier
        for identifier in _IDENTIFIER_PATTERN.findall(summary.lower())
        if "_" in identifier or "-" in identifier
    )


def _temporally_plausible(
    before: ObservableEvent | None,
    after: ObservableEvent | None,
) -> TemporalPlausibility:
    """Establish whether *before* occurred earlier than *after*.

    Timestamps are parsed as timezone-aware RFC3339 values and normalized to
    UTC before comparison so that ``10:00+08:00`` and ``02:00Z`` (the same
    instant) are treated consistently. When either timestamp is missing or
    unparseable the ordering is ``UNVERIFIABLE`` rather than guessed.
    """
    if before is None or after is None:
        return TemporalPlausibility.UNVERIFIABLE

    before_dt = _parse_rfc3339(before.occurred_at) if before.occurred_at else None
    after_dt = _parse_rfc3339(after.occurred_at) if after.occurred_at else None
    if before_dt is None or after_dt is None:
        return TemporalPlausibility.UNVERIFIABLE

    if before_dt < after_dt:
        return TemporalPlausibility.CONFIRMED
    return TemporalPlausibility.REJECTED


def _plausibility_is_plausible(plausibility: TemporalPlausibility) -> bool:
    """Backward-compatible bool view: only CONFIRMED counts as plausible."""
    return plausibility == TemporalPlausibility.CONFIRMED


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

    deduped = tuple(by_id.values())

    decisions_by_candidate: dict[str, set[ConfirmationDecision]] = {}
    for confirmation in deduped:
        decisions_by_candidate.setdefault(confirmation.candidate_id, set()).add(
            confirmation.decision
        )

    for candidate_id, decisions in decisions_by_candidate.items():
        if len(decisions) > 1:
            msg = (
                f"conflicting student confirmations for candidate {candidate_id}: "
                f"decisions {', '.join(sorted(d.value for d in decisions))}"
            )
            raise ValueError(msg)

    return deduped


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


def stable_candidate_id(
    draft: CandidateDraft,
    *,
    include_text_fields: bool = False,
    digest_len: int = 8,
) -> str:
    """Generate a stable candidate ID from draft content.



    The primary ID remains compatible with existing golden fixtures:
    ``node_type`` + sorted ``basis_event_ids``. When different drafts would
    otherwise collide on that base ID, callers can opt into hashing the text
    fields as well so semantically different candidates receive distinct,
    order-independent IDs; a longer ``digest_len`` makes the disambiguating
    digest effectively collision-free.

    """

    content_parts: list[object] = [draft.node_type.value, sorted(draft.basis_event_ids)]
    if include_text_fields:
        question = (
            draft.question_to_student.to_dict()
            if isinstance(draft.question_to_student, MissingInfo)
            else draft.question_to_student
        )
        content_parts.extend([draft.statement, draft.uncertainty, question])
    content = json.dumps(content_parts, sort_keys=True)

    suffix = _sha256_content(content)[:digest_len]

    for event_id in draft.basis_event_ids:
        match = _SCENARIO_ID_PATTERN.match(event_id)

        if match is not None:
            return f"cand-{match.group(1)}-{draft.node_type.value}-{suffix}"

    return f"cand-{draft.node_type.value}-{suffix}"


def _sha256_content(text: str) -> str:

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _materialize_candidates(
    drafts: tuple[CandidateDraft, ...],
    confirmations: tuple[StudentConfirmation, ...],
) -> tuple[LearningNodeCandidate, ...]:

    confirmation_ids = {confirmation.candidate_id for confirmation in confirmations}

    # Assign every draft a content-stable ID. The base ID (node_type + sorted
    # basis) is kept for golden-fixture compatibility; when several drafts
    # share that base, the disambiguating ID is derived from the full content
    # with a long digest, so it is deterministic and order-independent.
    ids: list[str] = []
    for draft in drafts:
        candidate_id = stable_candidate_id(draft)
        duplicate_bases = sum(1 for other in drafts if stable_candidate_id(other) == candidate_id)
        if duplicate_bases > 1:
            candidate_id = stable_candidate_id(draft, include_text_fields=True, digest_len=64)
        ids.append(candidate_id)

    # A remaining duplicate means the full-content digest itself collided (a
    # genuine SHA-256 collision). Do not fall back to an order-dependent suffix,
    # which would silently rebind a student confirmation; reject instead.
    seen_ids: set[str] = set()
    for i, candidate_id in enumerate(ids):
        if candidate_id in seen_ids:
            msg = (
                f"candidate id collision at index {i}: {candidate_id!r}; "
                "refusing to disambiguate by order as confirmations would "
                "silently rebind"
            )
            raise ValueError(msg)
        seen_ids.add(candidate_id)

    materialized: list[LearningNodeCandidate] = []
    for draft, candidate_id in zip(drafts, ids, strict=True):
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


def _limit_candidate_drafts(
    drafts: tuple[CandidateDraft, ...],
) -> tuple[tuple[CandidateDraft, ...], int]:
    if len(drafts) <= MAX_CANDIDATES:
        return drafts, 0

    ranked = sorted(
        enumerate(drafts),
        key=lambda item: (
            next(
                (
                    priority
                    for prefix, priority in _UNCERTAINTY_PRIORITY.items()
                    if item[1].uncertainty.startswith(prefix)
                ),
                len(_UNCERTAINTY_PRIORITY),
            ),
            item[0],
        ),
    )
    selected = tuple(draft for _, draft in ranked[:MAX_CANDIDATES])
    return selected, len(drafts) - len(selected)


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

    ai_trace_types = frozenset({"revise_ai_suggestion", "follow_up"})
    event_kind_by_id = {event.id: event.kind.value for event in bundle.events}
    for candidate in bundle.candidates:
        if candidate.node_type.value in ai_trace_types:
            has_trace = any(
                event_kind_by_id.get(bid) == "trace_record" for bid in candidate.basis_event_ids
            )
            if not has_trace:
                violations.append(
                    f"{candidate.id}: AI-type candidate ({candidate.node_type.value}) "
                    f"requires at least one trace_record in basis_event_ids"
                )
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
    task2_meta: dict[str, object] | None = None,
) -> ArchiveBundle:

    deduped_events = _dedupe_events(events)

    sorted_confirmations = _dedupe_confirmations(confirmations)

    if inferencer is not None:
        candidate_inferencer = inferencer

    else:
        from learntrace.reporting.llm import default_candidate_inferencer

        candidate_inferencer = default_candidate_inferencer()

    inferred_drafts = candidate_inferencer.infer(deduped_events)
    drafts, truncated_count = _limit_candidate_drafts(inferred_drafts)

    effective_warnings = warnings
    if truncated_count:
        effective_warnings = (
            *warnings,
            ArchiveWarning(
                code="candidate_limit_applied",
                source="candidate_inference",
                message=(
                    f"候选总量超过 {MAX_CANDIDATES} 条，已优先保留低/中不确定性候选，"
                    f"省略 {truncated_count} 条。"
                ),
            ),
        )

    candidates = _materialize_candidates(drafts, sorted_confirmations)

    bundle = ArchiveBundle(
        events=deduped_events,
        candidates=candidates,
        confirmations=sorted_confirmations,
        warnings=_dedupe_warnings(effective_warnings),
        inference_mode=str(getattr(candidate_inferencer, "inference_mode", "custom")),
        task2_meta=dict(task2_meta) if task2_meta is not None else {},
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


_TASK2_AUDIT_FIELDS = frozenset(
    ("parser_version", "analysis_scope", "inventory", "tool", "tool_version")
)


def archive_manifest(bundle: ArchiveBundle) -> dict[str, object]:
    """Return deterministic metadata for reproducing and auditing an archive."""

    record_sets = {
        "observable_fact": [event.to_dict() for event in bundle.events],
        "candidate_inference": [candidate.to_dict() for candidate in bundle.candidates],
        "student_confirmation": [confirmation.to_dict() for confirmation in bundle.confirmations],
        "warnings": [warning.to_dict() for warning in bundle.warnings],
    }

    manifest: dict[str, object] = {
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

    if bundle.task2_meta:
        # Preserve upstream Task 2 parsing context so the archive remains
        # auditable back to the parser version / analysis scope / inventory.
        manifest["task2_meta"] = {
            key: bundle.task2_meta[key] for key in _TASK2_AUDIT_FIELDS if key in bundle.task2_meta
        }

    return manifest


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
        "learntrace_bundle": True,
        "archive_version": ARCHIVE_VERSION,
        "archive_manifest": archive_manifest(bundle),
        "candidate_inference_mode": bundle.inference_mode,
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
