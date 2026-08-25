"""Deterministic Task 4 pipeline for candidate inference and confirmation handling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, cast

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
MAX_CANDIDATES = 10
_MAX_TRACE_COMMIT_GAP_SECONDS = 30 * 60
_MAX_CROSS_SESSION_GAP_SECONDS = 24 * 60 * 60
_OPENCODE_SESSION_RE = re.compile(r"^trace://opencode/(?P<session>[^/]+)/")

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
_LOW_SIGNAL_TRACE_TERMS = (
    "命令类型：git add",
    "命令类型：git log",
    "命令类型：git status",
    "命令类型：kill",
    "命令类型：ls",
    "命令类型：node",
    "命令类型：pgrep",
    "命令类型：pkill",
    "命令类型：rm",
    "命令类型：ss",
    "工具 ls",
    "工具 node",
    "工具 rm",
    "工具 todowrite",
)
_CHANGE_MARKERS = (
    "改",
    "调整",
    "替换",
    "重构",
    "重写",
    "采用",
    "instead",
    "replace",
    "refactor",
    "rewrite",
    "switch",
)
_AI_SUGGESTION_TERMS = (
    "建议",
    "提议",
    "推荐",
    "suggest",
    "suggested",
    "recommend",
    "recommended",
    "proposal",
)
_TRACE_LEARNING_SIGNAL_TERMS = (
    *_AI_SUGGESTION_TERMS,
    "失败",
    "错误",
    "修复",
    "测试",
    "追问",
    "决定",
    "选择",
    "error",
    "fail",
    "fix",
    "question",
    "test",
    "pytest",
)
_TOPIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "arithmetic": ("divide", "division", "test_div", "zero", "除零", "除数"),
    "backend": ("backend", "server", "controller", "后端", "接口", "服务"),
    "cli": ("argument", "cli", "option", "命令行", "参数", "选项"),
    "configuration": ("config", "configuration", "setting", "配置", "设置"),
    "documentation": ("documentation", "readme", "文档", "说明"),
    "frontend": ("frontend", "jsx", "react", "tsx", "ui", "vue", "前端", "页面", "组件"),
    "grading": ("grade", "score", "成绩", "等级", "百分", "评分", "统计"),
    "missing_data": ("missing", "none", "null", "缺失", "空值", "空行"),
    "parsing": (
        "column",
        "csv",
        "dtype",
        "infer",
        "load",
        "parse",
        "parser",
        "read_csv",
        "tabular",
        "type",
        "解析",
        "读取",
        "类型",
    ),
    "testing": ("pytest", "test", "测试", "用例", "覆盖", "断言"),
    "validation": (
        "check",
        "isdigit",
        "regex",
        "regexp",
        "validate",
        "validation",
        "编号",
        "学号",
        "数字",
        "校验",
        "检查",
        "格式",
        "正则",
    ),
}
_ASCII_TOPIC_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_GENERIC_TOPIC_TERMS = frozenset(
    {
        "commit",
        "completed",
        "file",
        "git",
        "opencode",
        "src",
        "tool",
        "tools",
    }
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
        if (
            len(traces) >= 2
            and not commits
            and not documents
            and _topic_match_score(traces[0].summary, traces[1].summary) > 0
            and (
                not _opencode_session_ids(traces[0])
                or not _opencode_session_ids(traces[1])
                or _opencode_session_ids(traces[0]) == _opencode_session_ids(traces[1])
            )
        ):
            drafts.append(self._follow_up_candidate(traces[0], traces[1]))

        # A project may span several exported OpenCode sessions. Keep session
        # provenance in source refs and only connect two sessions when their
        # timestamps fall within a conservative window and they share a
        # semantic project topic. Generic repeated tool names are insufficient.
        drafts.extend(self._cross_session_follow_ups(traces))

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
                if not precedes(failing_log, commit) or not _topics_related(
                    failing_log.summary, commit.summary
                ):
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
            if (_looks_like_fix_commit(commit) and not test_logs) or _looks_like_rewrite(commit):
                drafts.append(self._commit_only_fix_candidate(commit))
            elif _looks_like_constraint_change(commit):
                drafts.append(self._commit_only_constraint_candidate(commit))

        return tuple(drafts)

    def _cross_session_follow_ups(
        self,
        traces: tuple[ObservableEvent, ...],
    ) -> tuple[CandidateDraft, ...]:
        follow_ups: list[CandidateDraft] = []
        used_later_ids: set[str] = set()
        for later in traces:
            if later.id in used_later_ids:
                continue
            later_sessions = _opencode_session_ids(later)
            if not later_sessions:
                continue
            matches: list[tuple[int, float, ObservableEvent]] = []
            for earlier in traces:
                earlier_sessions = _opencode_session_ids(earlier)
                if not earlier_sessions or earlier_sessions == later_sessions:
                    continue
                if not (_has_trace_learning_signal(earlier) or _has_trace_learning_signal(later)):
                    continue
                gap = _trace_commit_gap_seconds(earlier, later)
                shared_topics = _semantic_topics(earlier.summary) & _semantic_topics(later.summary)
                if gap is not None and gap <= _MAX_CROSS_SESSION_GAP_SECONDS and shared_topics:
                    matches.append((len(shared_topics), gap, earlier))
            if not matches:
                continue
            _, _, earlier = min(
                matches,
                key=lambda item: (-item[0], item[1], item[2].id),
            )
            used_later_ids.add(later.id)
            follow_ups.append(self._cross_session_follow_up_candidate(earlier, later))
        return tuple(follow_ups)

    @staticmethod
    def _cross_session_follow_up_candidate(
        earlier: ObservableEvent,
        later: ObservableEvent,
    ) -> CandidateDraft:
        return CandidateDraft(
            node_type=NodeType.FOLLOW_UP,
            statement="学生可能在后续 OpenCode 会话中围绕同一主题继续推进工作。",
            basis_event_ids=(earlier.id, later.id),
            uncertainty=(
                "中：两次会话在时间窗内共享项目主题，且至少一条轨迹具有测试、"
                "失败、修复或决策信号；是否构成连续学习过程仍需学生确认。"
            ),
            question_to_student=(
                "后一个会话是否延续了前一个会话中的问题？你具体推进或验证了什么？"
            ),
        )

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
            if trace.id not in used_event_ids
            and not _is_low_signal_trace(trace)
            and _has_trace_learning_signal(trace)
        ]
        follow_ups: list[CandidateDraft] = []
        for commit in commits:
            if commit.id in used_event_ids:
                continue
            preceding: list[tuple[float, ObservableEvent, int]] = []
            for trace in available:
                gap_seconds = _trace_commit_gap_seconds(trace, commit)
                relevance = _topic_match_score(trace.summary, commit.summary)
                if (
                    gap_seconds is not None
                    and gap_seconds <= _MAX_TRACE_COMMIT_GAP_SECONDS
                    and relevance > 0
                ):
                    preceding.append((gap_seconds, trace, relevance))
            if not preceding:
                continue
            _, trace, relevance = min(
                preceding,
                key=lambda item: (-item[2], item[0], item[1].id),
            )
            available.remove(trace)
            follow_ups.append(
                self._trace_commit_follow_up_candidate(trace, commit, relevance=relevance)
            )
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

        return 0

    def _revise_ai_matches(self, trace: ObservableEvent, commit: ObservableEvent) -> bool:
        """Whether a trace/commit pair is a topical AI-suggestion revision.

        The temporal ordering is applied by the caller (``precedes``); here we
        only check that the summaries actually correspond, so unrelated nearby
        commits are not turned into a revision claim.
        """
        trace_lowered = trace.summary.casefold()
        has_explicit_suggestion = any(
            _marker_present(trace_lowered, term) for term in _AI_SUGGESTION_TERMS
        )
        return (
            has_explicit_suggestion
            and _topics_related(trace.summary, commit.summary)
            and _contains_change_marker(_commit_intent_text(commit.summary))
        )

    def _revise_ai_candidate(
        self,
        trace: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:

        plausible = _plausibility_is_plausible(_temporally_plausible(trace, commit))

        shared_topics = _semantic_topics(trace.summary) & _semantic_topics(commit.summary)
        if "parsing" in shared_topics:
            return CandidateDraft(
                node_type=NodeType.REVISE_AI_SUGGESTION,
                statement="学生可能没有直接采用 AI 的数据解析建议，而是选择了另一种解析策略。",
                basis_event_ids=(trace.id, commit.id),
                uncertainty=(
                    "中：AI 建议与代码提交是两条独立记录，系统不预设二者相关；"
                    "是否参考及修改动机均需学生确认。"
                    if plausible
                    else "高：时间顺序无法验证，AI 建议与代码修改的关联性存疑。"
                ),
                question_to_student="这次数据解析实现是否参考并调整了 AI 的建议？",
            )

        if "validation" in shared_topics:
            return CandidateDraft(
                node_type=NodeType.REVISE_AI_SUGGESTION,
                statement="学生可能调整了 AI 建议的输入校验方法，并采用了不同实现。",
                basis_event_ids=(trace.id, commit.id),
                uncertainty=(
                    "中：AI 建议与代码提交是两条独立记录，系统不预设二者相关；"
                    "两种实现语义相近，是否参考需学生确认。"
                    if plausible
                    else "高：时间顺序无法验证，语义近似的修改可能来自其他原因。"
                ),
                question_to_student="这次输入校验实现是否参考并调整了 AI 的建议？",
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

        if "arithmetic" in (_semantic_topics(test_log.summary) & _semantic_topics(commit.summary)):
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

        if _semantic_topics(commit.summary) & {"missing_data", "parsing"}:
            return CandidateDraft(
                node_type=NodeType.ADD_TESTS,
                statement="学生可能主动补充了边界或缺失输入测试。",
                basis_event_ids=(commit.id, test_log.id),
                uncertainty="低：提交内容即为新测试文件且全部通过，意图明确。",
                question_to_student="这些边界或缺失输入用例是你主动识别并补充的吗？",
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

        shared_topics = _semantic_topics(first_trace.summary) & _semantic_topics(
            second_trace.summary
        )
        if "grading" in shared_topics and "missing_data" in _semantic_topics(second_trace.summary):
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
        *,
        relevance: int,
    ) -> CandidateDraft:
        return CandidateDraft(
            node_type=NodeType.FOLLOW_UP,
            statement="学生可能在使用编码助手完成一项工具操作后继续推进，并形成了后续代码提交。",
            basis_event_ids=(trace.id, commit.id),
            uncertainty=(
                f"高：轨迹与提交具有主题关联（相关性得分 {relevance}）且时间邻近，"
                "但工具操作目的、提交内容与学习收获仍需学生确认。"
            ),
            question_to_student="这次工具操作是否帮助你推进了后续提交？你从中形成了什么新理解？",
        )

    def _adjust_constraints_candidate(
        self,
        document: ObservableEvent,
        commit: ObservableEvent,
    ) -> CandidateDraft:

        plausible = _plausibility_is_plausible(_temporally_plausible(document, commit))

        if "grading" in (_semantic_topics(document.summary) & _semantic_topics(commit.summary)):
            return CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能因为评分或统计约束变化而重写了相关逻辑。",
                basis_event_ids=(document.id, commit.id),
                uncertainty=(
                    "中：文档与提交时间相邻，但文档更新者身份未记录，无法确认是学生本人调整。"
                    if plausible
                    else "高：文档与提交时间顺序无法验证，事件关联匹配可能是偶然。"
                ),
                question_to_student="这项评分或统计约束是你主动调整的，还是外部要求变更？",
            )

        return CandidateDraft(
            node_type=NodeType.ADJUST_CONSTRAINTS,
            statement="学生可能因为设计约束变化而调整了实现。",
            basis_event_ids=(document.id, commit.id),
            uncertainty="中：文档变化与代码调整相邻，但约束来源仍需学生说明。",
            question_to_student="这次约束调整是你主动提出的，还是来自外部要求？",
        )

    def _commit_only_fix_candidate(self, commit: ObservableEvent) -> CandidateDraft:

        if _looks_like_fix_commit(commit):
            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="提交记录表明学生可能修复了一个实现问题。",
                basis_event_ids=(commit.id,),
                uncertainty="高：只有修复提交，缺少失败日志和测试结果，问题表现与验证过程仍未知。",
                question_to_student="这次修复前出现了什么可复现问题，你如何验证修改有效？",
            )

        if "parsing" in _semantic_topics(commit.summary):
            return CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能重写了解析实现，并替换了先前的处理方案。",
                basis_event_ids=(commit.id,),
                uncertainty="高：无任何测试日志或轨迹记录初始方案失败，仅有单次提交，失败假设无法核实。",
                question_to_student="重写解析实现之前，原方案是否遇到过失败？",
            )

        return CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能放弃了之前的方案并改写为新的实现。",
            basis_event_ids=(commit.id,),
            uncertainty="高：仅有一次提交，缺少失败日志或轨迹来解释改写原因。",
            question_to_student="这次大幅改写之前，原方案是否遇到过问题？",
        )

    def _commit_only_constraint_candidate(self, commit: ObservableEvent) -> CandidateDraft:

        if "missing_data" in _semantic_topics(commit.summary):
            return CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能调整了缺失输入的处理约束。",
                basis_event_ids=(commit.id,),
                uncertainty="高：仅有单次提交，无测试日志、无轨迹、无文档，约束调整的原因与过程均不可知。",
                question_to_student="调整缺失输入的处理方式是出于什么考虑？",
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


def _opencode_session_ids(event: ObservableEvent) -> frozenset[str]:
    sessions: set[str] = set()
    for source_ref in event.source_refs:
        match = _OPENCODE_SESSION_RE.match(source_ref.ref)
        if match is not None:
            sessions.add(match.group("session"))
    return frozenset(sessions)


def _is_low_signal_trace(trace: ObservableEvent) -> bool:
    lowered = trace.summary.casefold()
    return any(term.casefold() in lowered for term in _LOW_SIGNAL_TRACE_TERMS)


def _has_trace_learning_signal(trace: ObservableEvent) -> bool:
    """Require content beyond a generic completed tool operation.

    Task 3 intentionally minimizes traces. A tool name, command class, or path
    alone does not show a learning decision and must not be paired with a
    nearby commit merely to manufacture a candidate.
    """

    lowered = trace.summary.casefold()
    return any(_marker_present(lowered, term) for term in _TRACE_LEARNING_SIGNAL_TERMS)


def _semantic_topics(summary: str) -> frozenset[str]:
    lowered = summary.casefold()
    lexical_terms = _lexical_topics(summary)
    return frozenset(
        topic
        for topic, markers in _TOPIC_FAMILIES.items()
        if any(
            _marker_present(lowered, marker)
            or (marker.isascii() and marker.casefold() in lexical_terms)
            for marker in markers
        )
    )


def _marker_present(lowered_summary: str, marker: str) -> bool:
    if marker.isascii():
        return (
            re.search(
                rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])",
                lowered_summary,
            )
            is not None
        )
    return marker in lowered_summary


def _lexical_topics(summary: str) -> frozenset[str]:
    terms: set[str] = set()
    for token in _ASCII_TOPIC_RE.findall(summary):
        # Preserve word boundaries in Python/JavaScript-style error and symbol
        # names before case-folding: ``ZeroDivisionError`` becomes
        # ``zero``, ``division``, ``error`` instead of one opaque token.
        expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", token)
        terms.update(part.casefold() for part in re.split(r"[_-]+", expanded) if len(part) >= 3)
    return frozenset(terms - _GENERIC_TOPIC_TERMS)


def _topic_match_score(first: str, second: str) -> int:
    shared_families = _semantic_topics(first) & _semantic_topics(second)
    shared_terms = _lexical_topics(first) & _lexical_topics(second)
    return len(shared_families) * 10 + len(shared_terms)


def _topics_related(first: str, second: str) -> bool:
    return _topic_match_score(first, second) > 0


def _contains_change_marker(summary: str) -> bool:
    lowered = summary.casefold()
    return any(_marker_present(lowered, marker) for marker in _CHANGE_MARKERS)


_QUOTED_COMMIT_MESSAGE_RE = re.compile(r"提交信息为[‘'“\"](?P<message>.*?)[’'”\"]")
_COMMIT_PREFIX_RE = re.compile(r"^(?:提交|commit)\s+[a-f0-9]+\s*[：:]\s*", re.IGNORECASE)


def _commit_intent_text(summary: str) -> str:
    """Strip parser boilerplate before classifying the commit's intent."""

    quoted = _QUOTED_COMMIT_MESSAGE_RE.search(summary)
    if quoted is not None:
        return quoted.group("message")
    return _COMMIT_PREFIX_RE.sub("", summary, count=1)


def _constraint_terms(summary: str) -> frozenset[str]:
    return frozenset(term for term in _CONSTRAINT_TERMS if term in summary)


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

    intent = _commit_intent_text(commit.summary)
    return any(term in intent for term in ("重写", "去掉", "改写"))


def _looks_like_fix_commit(commit: ObservableEvent) -> bool:
    intent = _commit_intent_text(commit.summary).casefold().strip()
    return (
        re.match(r"^fix(?:\([^)]*\))?!?(?:\s*:|\s+)", intent) is not None
        or "修复" in intent
        or "纠正" in intent
    )


def _looks_like_constraint_change(commit: ObservableEvent) -> bool:
    intent = _commit_intent_text(commit.summary)
    topics = _semantic_topics(intent)
    return (
        "约束" in intent
        or "边界" in intent
        or bool(topics & {"cli", "missing_data"})
        and _contains_change_marker(intent)
    )


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
) -> str:
    """Generate an order- and candidate-set-independent ID from draft content."""

    content_parts: list[object] = [
        draft.node_type.value,
        sorted(draft.basis_event_ids),
        draft.statement,
    ]
    content = json.dumps(content_parts, sort_keys=True)

    suffix = _sha256_content(content)[:16]

    for event_id in sorted(draft.basis_event_ids):
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

    ids = [stable_candidate_id(draft) for draft in drafts]

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


def _dedupe_candidate_drafts(
    drafts: tuple[CandidateDraft, ...],
) -> tuple[tuple[CandidateDraft, ...], int]:
    """Merge repeated questions while preserving all distinct evidence."""

    merged: dict[tuple[NodeType, str, str, str], CandidateDraft] = {}
    for draft in drafts:
        question = (
            draft.question_to_student
            if isinstance(draft.question_to_student, str)
            else json.dumps(draft.question_to_student.to_dict(), ensure_ascii=False, sort_keys=True)
        )
        key = (
            draft.node_type,
            draft.statement.strip(),
            question.strip(),
            draft.uncertainty.strip(),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = draft
            continue
        merged[key] = replace(
            existing,
            basis_event_ids=tuple(sorted({*existing.basis_event_ids, *draft.basis_event_ids})),
        )
    unique = tuple(merged.values())
    return unique, len(drafts) - len(unique)


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

    from learntrace.reporting.llm import OpenAIChatCandidateInferencer

    configured_inference_mode = str(getattr(candidate_inferencer, "inference_mode", "custom"))
    supports_llm_fallback = isinstance(candidate_inferencer, OpenAIChatCandidateInferencer)
    inference_failure_warnings: tuple[ArchiveWarning, ...] = ()
    try:
        inferred_drafts = candidate_inferencer.infer(deduped_events)
    except Exception as exc:
        # Keep ordinary custom-inferencer failures visible. Only the explicit
        # remote LLM adapter receives the documented local fallback.
        from learntrace.reporting.llm import LLMInferenceError

        if not supports_llm_fallback or not isinstance(exc, LLMInferenceError):
            raise
        inferred_drafts = ()
        failure_code = str(getattr(exc, "code", "llm_inference_failed"))
        failure_detail = str(exc).strip()
        inference_failure_warnings = (
            ArchiveWarning(
                code=failure_code,
                source="candidate_inference",
                message=(
                    "LLM 候选推断失败；已改用本地确定性规则。"
                    + (f" 原因：{failure_detail}" if failure_detail else "")
                ),
            ),
        )

    fallback_used = supports_llm_fallback and not inferred_drafts and bool(deduped_events)
    if fallback_used:
        inferred_drafts = StubCandidateInferencer().infer(deduped_events)
    unique_drafts, duplicate_count = _dedupe_candidate_drafts(inferred_drafts)
    drafts, truncated_count = _limit_candidate_drafts(unique_drafts)

    warning_provider = cast(
        Callable[[], tuple[ArchiveWarning, ...]] | None,
        getattr(candidate_inferencer, "inference_warnings", None),
    )
    inference_warnings = tuple(warning_provider()) if callable(warning_provider) else ()
    effective_warnings = (*warnings, *inference_failure_warnings, *inference_warnings)
    if fallback_used:
        effective_warnings = (
            *effective_warnings,
            ArchiveWarning(
                code="llm_fallback_to_stub",
                source="candidate_inference",
                message="LLM 未提供可用候选；本次候选已由本地确定性规则补充。",
            ),
        )
    if duplicate_count:
        effective_warnings = (
            *effective_warnings,
            ArchiveWarning(
                code="duplicate_candidates_removed",
                source="candidate_inference",
                message=f"已移除 {duplicate_count} 条重复候选问题。",
            ),
        )
    if truncated_count:
        effective_warnings = (
            *effective_warnings,
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
        inference_mode=("llm_stub_fallback" if fallback_used else configured_inference_mode),
        task2_meta=dict(task2_meta) if task2_meta is not None else {},
    )

    validate_bundle(bundle, validator=validator)

    return bundle


def apply_confirmations(
    bundle: ArchiveBundle,
    confirmations: tuple[StudentConfirmation, ...],
    *,
    validator: ContractValidator | None = None,
) -> ArchiveBundle:
    """Resolve a persisted candidate snapshot without running inference again."""

    merged_confirmations = _dedupe_confirmations((*bundle.confirmations, *confirmations))
    confirmed_candidate_ids = {confirmation.candidate_id for confirmation in merged_confirmations}
    candidates = tuple(
        replace(
            candidate,
            status=(
                CandidateStatus.RESOLVED
                if candidate.id in confirmed_candidate_ids
                else CandidateStatus.PROPOSED
            ),
        )
        for candidate in bundle.candidates
    )
    updated = replace(
        bundle,
        candidates=candidates,
        confirmations=merged_confirmations,
    )
    validate_bundle(updated, validator=validator)
    return updated


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


@dataclass(frozen=True, slots=True)
class EvidenceGapSpec:
    """Canonical wording of one mechanically confirmable evidence gap.

    Both consumers of the gap concept read from here: the default reporting
    path asks the student ``question`` (with ``uncertainty``); the optional
    evidence-state frontend records ``statement`` as the gap marker. Keeping
    the two wordings in one place is what stops them drifting apart.
    """

    key: str
    label: str
    required_kind: EventKind
    reason: str
    statement: str
    question: str
    uncertainty: str


#: The two evidence gaps the archive can mechanically confirm from its event
#: set: a test run left no record, or the project goal was never recorded.
EVIDENCE_GAP_SPECS: tuple[EvidenceGapSpec, ...] = (
    EvidenceGapSpec(
        key="gap-test-evidence",
        label="测试运行记录",
        required_kind=EventKind.TEST_LOG,
        reason="missing_source",
        statement="没有发现测试运行记录；是否运行过测试无法从现有证据判断。",
        question="当前材料中没有发现测试运行记录。你是否运行过测试，结果如何？",
        uncertainty="高：系统只能确认测试证据未记录，不能判断测试是否实际运行。",
    ),
    EvidenceGapSpec(
        key="gap-project-goal",
        label="项目目标记录",
        required_kind=EventKind.DOCUMENT,
        reason="missing_source",
        statement="没有明确记录项目目标；缺少可引用的任务书或要求类文档。",
        question="当前材料没有明确记录项目目标，请补充本次任务目标。",
        uncertainty="高：缺少可引用的任务书、README 或要求类文档。",
    ),
)


def fallback_reflection_questions(bundle: ArchiveBundle) -> tuple[dict[str, object], ...]:
    """Return evidence-gap questions when inference produced no candidates.

    These questions are deliberately *not* materialized as learning-node
    candidates: they ask the student to supply missing facts without claiming
    that a learning moment already occurred.
    """

    if bundle.candidates:
        return ()

    kinds = {event.kind for event in bundle.events}
    questions: list[dict[str, object]] = [
        {
            "question_id": spec.key,
            "question_type": "evidence_gap",
            "candidate_id": None,
            "node_type": None,
            "question_to_student": spec.question,
            "basis_event_ids": [],
            "uncertainty": spec.uncertainty,
        }
        for spec in EVIDENCE_GAP_SPECS
        if spec.required_kind not in kinds
    ]
    questions.append(
        {
            "question_id": "gap-learning-reflection",
            "question_type": "reflection",
            "candidate_id": None,
            "node_type": None,
            "question_to_student": (
                "当前证据未形成明确的学习节点。你认为本次最重要的调整是什么，又是如何验证它的？"
            ),
            "basis_event_ids": [],
            "uncertainty": "高：该问题用于收集学生原话，不代表系统已经推断出学习结论。",
        }
    )
    return tuple(questions)


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

    pending_questions: list[dict[str, object]] = [
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
    pending_questions.extend(fallback_reflection_questions(bundle))

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
