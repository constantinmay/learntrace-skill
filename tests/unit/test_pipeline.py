# pyright: reportPrivateUsage=false
from __future__ import annotations

from typing import Any, cast

import pytest

from learntrace.models import (
    ConfirmationDecision,
    EventKind,
    NodeType,
    ObservableEvent,
    SourceRef,
    SourceType,
    StudentConfirmation,
)
from learntrace.reporting import CandidateDraft, build_archive_bundle
from learntrace.reporting.pipeline import (
    MAX_CANDIDATES,
    StubCandidateInferencer,
    TemporalPlausibility,
    _temporally_plausible,
    archive_manifest,
)


def _event(
    record_id: str,
    kind: EventKind,
    summary: str,
    *,
    occurred_at: str | None = None,
) -> ObservableEvent:
    return ObservableEvent(
        id=record_id,
        kind=kind,
        summary=summary,
        source_refs=(SourceRef(type=SourceType.FILE, ref=f"{record_id}.txt"),),
        occurred_at=occurred_at,
    )


def _confirmation(
    record_id: str,
    candidate_id: str,
    decision: ConfirmationDecision,
) -> StudentConfirmation:
    return StudentConfirmation(
        id=record_id,
        candidate_id=candidate_id,
        decision=decision,
        student_statement="学生自述。",
    )


class DuplicateScenarioInferencer:
    def __init__(self, reverse: bool = False) -> None:
        self._reverse = reverse

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        commit = next(event for event in events if event.kind == EventKind.GIT_COMMIT)
        drafts = (
            CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能先修复了空行读取导致的失败。",
                basis_event_ids=(commit.id,),
                uncertainty="高：只有提交记录，仍需学生确认真实动机。",
                question_to_student="这次改动是否是为了修复空行读取失败？",
            ),
            CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="学生可能在同一次提交里顺手整理了错误处理分支。",
                basis_event_ids=(commit.id,),
                uncertainty="高：只有提交记录，无法从外部证据确认是否属于独立学习节点。",
                question_to_student="这次提交里是否还顺手整理了错误处理分支？",
            ),
        )
        if self._reverse:
            return tuple(reversed(drafts))
        return drafts


def test_stable_candidate_ids_disambiguate_same_basis_by_content() -> None:
    events = (_event("evt-sx-1", EventKind.GIT_COMMIT, "提交 a1b2c3d：重写空行处理逻辑。"),)

    bundle = build_archive_bundle(events, inferencer=DuplicateScenarioInferencer())
    reversed_bundle = build_archive_bundle(
        events,
        inferencer=DuplicateScenarioInferencer(reverse=True),
    )

    ids_by_statement = {candidate.statement: candidate.id for candidate in bundle.candidates}
    reversed_ids_by_statement = {
        candidate.statement: candidate.id for candidate in reversed_bundle.candidates
    }

    assert len(set(ids_by_statement.values())) == 2
    assert ids_by_statement == reversed_ids_by_statement


class IdenticalCandidateInferencer:
    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        commit = next(event for event in events if event.kind == EventKind.GIT_COMMIT)
        draft = CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="identical statement",
            basis_event_ids=(commit.id,),
            uncertainty="高：只有提交记录。",
            question_to_student="这是否为了修复？",
        )
        return (draft, draft)


def test_identical_candidates_rejected_not_order_disambiguated() -> None:
    """Two byte-identical candidates still collide even after full content
    hashing; they must be rejected, never disambiguated by order, because an
    order-based suffix would silently rebind a confirmation."""
    events = (_event("evt-sy-1", EventKind.GIT_COMMIT, "提交 a1b2c3d：重写。"),)

    with pytest.raises(ValueError, match="candidate id collision"):
        build_archive_bundle(events, inferencer=IdenticalCandidateInferencer())


def test_english_commit_overview_is_recognized() -> None:
    events = (
        _event(
            "evt-demo-commit",
            EventKind.GIT_COMMIT,
            "Commit f6a7b8c: add tests/test_parser_edge.py to cover empty rows.",
        ),
        _event("evt-demo-test", EventKind.TEST_LOG, "pytest passed edge cases."),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "add_tests"


class SingleCommitInferencer:
    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        commit = next(event for event in events if event.kind == EventKind.GIT_COMMIT)
        return (
            CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement="学生可能调整了输入约束。",
                basis_event_ids=(commit.id,),
                uncertainty="高：仅有单次提交。",
                question_to_student="这次调整的考虑是什么？",
            ),
        )


def _commit(id_: str, occurred_at: str) -> ObservableEvent:
    return _event(id_, EventKind.GIT_COMMIT, "提交 a1b2c3d：调整参数。", occurred_at=occurred_at)


def _bundle_candidate_id(inferencer: SingleCommitInferencer, commit: ObservableEvent) -> str:
    bundle = build_archive_bundle((commit,), inferencer=inferencer)
    return bundle.candidates[0].id


def test_confirmations_cannot_conflict_on_same_candidate() -> None:
    commit = _commit("evt-c01-1", "2026-05-01T10:00:00+08:00")
    inferencer = SingleCommitInferencer()
    candidate_id = _bundle_candidate_id(inferencer, commit)

    confirmations = (
        _confirmation("c-1", candidate_id, ConfirmationDecision.CONFIRMED),
        _confirmation("c-2", candidate_id, ConfirmationDecision.DENIED),
    )

    with pytest.raises(ValueError, match="conflicting student confirmations for candidate"):
        build_archive_bundle((commit,), confirmations=confirmations, inferencer=inferencer)


def test_duplicate_identical_confirmations_are_deduplicated() -> None:
    commit = _commit("evt-c02-1", "2026-05-01T10:00:00+08:00")
    inferencer = SingleCommitInferencer()
    candidate_id = _bundle_candidate_id(inferencer, commit)

    confirmations = (
        _confirmation("c-1", candidate_id, ConfirmationDecision.CONFIRMED),
        _confirmation("c-1", candidate_id, ConfirmationDecision.CONFIRMED),
    )

    bundle = build_archive_bundle((commit,), confirmations=confirmations, inferencer=inferencer)

    assert len(bundle.confirmations) == 1
    assert bundle.candidates[0].status.value == "resolved"


def _evt(
    id_: str, occurred_at: str | None, kind: EventKind = EventKind.GIT_COMMIT
) -> ObservableEvent:
    return _event(id_, kind, "提交 a1b2c3d：概要。", occurred_at=occurred_at)


def test_temporal_plausibility_normalizes_timezones() -> None:
    # 10:00+08:00 == 02:00Z, which is BEFORE 03:00Z the same day. The naive
    # RFC3339 string comparison ("1" > "0" after the "T") would wrongly report
    # this as not-plausible; timezone-aware comparison must say CONFIRMED.
    before = _evt("e1", "2026-05-01T10:00:00+08:00")
    after = _evt("e2", "2026-05-01T03:00:00Z")
    assert _temporally_plausible(before, after) == TemporalPlausibility.CONFIRMED

    # Reversed ordering (before later than after) must be REJECTED.
    before = _evt("e1", "2026-05-01T03:00:00Z")
    after = _evt("e2", "2026-05-01T10:00:00+08:00")
    assert _temporally_plausible(before, after) == TemporalPlausibility.REJECTED


def test_temporal_plausibility_is_unverifiable_without_timestamps() -> None:
    assert _temporally_plausible(_evt("e1", None), _evt("e2", None)) == (
        TemporalPlausibility.UNVERIFIABLE
    )
    assert _temporally_plausible(_evt("e1", "2026-05-01T10:00:00Z"), _evt("e2", None)) == (
        TemporalPlausibility.UNVERIFIABLE
    )


def test_archive_manifest_preserves_task2_meta() -> None:
    commit = _commit("evt-c03-1", "2026-05-01T10:00:00+08:00")
    bundle = build_archive_bundle(
        (commit,),
        inferencer=SingleCommitInferencer(),
        task2_meta={
            "parser_version": "v0.3.1",
            "analysis_scope": {"dirs": ["src"]},
            "inventory": {"files": 12},
        },
    )

    manifest = cast(dict[str, Any], archive_manifest(bundle))

    assert manifest["task2_meta"]["parser_version"] == "v0.3.1"
    assert manifest["task2_meta"]["analysis_scope"] == {"dirs": ["src"]}
    assert manifest["task2_meta"]["inventory"] == {"files": 12}


def test_archive_manifest_empty_when_no_task2_meta() -> None:
    commit = _commit("evt-c04-1", "2026-05-01T10:00:00+08:00")
    bundle = build_archive_bundle((commit,), inferencer=SingleCommitInferencer())

    assert "task2_meta" not in archive_manifest(bundle)


def test_file_level_commit_changes_not_treated_as_overview() -> None:
    """Task2-style file-level events must not be used as commit overviews."""
    events = (
        _event(
            "evt-git-file-1",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d 修改文件 src/math.py（新增 2 行、删除 0 行）。",
        ),
        _event(
            "evt-git-2",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d 的提交信息为 fix: 修复除零错误。",
        ),
        _event("evt-test-1", EventKind.TEST_LOG, "测试日志记录用例 test_div 出现失败。"),
    )
    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())
    assert len(bundle.candidates) == 1
    assert "evt-git-2" in bundle.candidates[0].basis_event_ids
    assert "evt-git-file-1" not in bundle.candidates[0].basis_event_ids


def test_stub_binds_to_failing_log_and_can_emit_multiple() -> None:
    """The stub must reference the FAILING log (not the first test log) and
    accumulate multiple candidates instead of early-returning one."""
    events = (
        _event(
            "evt-pass-1",
            EventKind.TEST_LOG,
            "已有测试日志记录：2 个通过，耗时 1 秒。",
            occurred_at="2026-05-02T10:00:00+08:00",
        ),
        _event(
            "evt-fail-1",
            EventKind.TEST_LOG,
            "测试日志记录用例 test_div 出现失败。",
            occurred_at="2026-05-02T11:00:00+08:00",
        ),
        _event(
            "evt-commit-1",
            EventKind.GIT_COMMIT,
            "提交 d9e0f1a：修复除零错误，divide 返回 None 保护。",
            occurred_at="2026-05-02T12:00:00+08:00",
        ),
    )
    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    candidate = bundle.candidates[0]
    assert candidate.node_type.value == "fix_failed_approach"
    assert "evt-fail-1" in candidate.basis_event_ids
    assert "evt-pass-1" not in candidate.basis_event_ids


def test_stub_does_not_generate_fix_candidate_when_commit_precedes_failure() -> None:
    """REJECTED ordering (failure after commit) must not yield a fix candidate:
    the commit cannot be presented as following the failure."""
    events = (
        _event(
            "evt-commit-1",
            EventKind.GIT_COMMIT,
            "提交 d9e0f1a：修复除零错误，divide 返回 None 保护。",
            occurred_at="2026-05-02T09:00:00+08:00",
        ),
        _event(
            "evt-fail-1",
            EventKind.TEST_LOG,
            "测试日志记录用例 test_div 出现失败。",
            occurred_at="2026-05-02T12:00:00+08:00",
        ),
    )
    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert bundle.candidates == ()


def test_stub_adjust_constraints_selects_one_topical_document_per_commit() -> None:
    events = (
        _event(
            "evt-doc-grade",
            EventKind.DOCUMENT,
            "设计文档更新：成绩上限由百分制调整为等级制。",
        ),
        _event(
            "evt-doc-time",
            EventKind.DOCUMENT,
            "接口文档约定 created_at 字段使用统一时间格式。",
        ),
        _event(
            "evt-doc-unrelated",
            EventKind.DOCUMENT,
            "项目说明记录了目录结构和启动步骤。",
        ),
        _event(
            "evt-commit-grade",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：统计逻辑按等级制重写，移除百分制边界判断。",
        ),
        _event(
            "evt-commit-time",
            EventKind.GIT_COMMIT,
            "提交 d4e5f6a：统一 created_at 字段格式。",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())
    constraint_candidates = [
        candidate
        for candidate in bundle.candidates
        if candidate.node_type == NodeType.ADJUST_CONSTRAINTS
    ]

    assert len(constraint_candidates) == 2
    assert {candidate.basis_event_ids for candidate in constraint_candidates} == {
        ("evt-doc-grade", "evt-commit-grade"),
        ("evt-doc-time", "evt-commit-time"),
    }


def test_stub_links_minimal_trace_to_nearest_following_commit() -> None:
    events = (
        _event(
            "evt-trace-write",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 write 已完成。路径：frontend/src/App.tsx。",
            occurred_at="2026-08-10T09:40:00Z",
        ),
        _event(
            "evt-trace-edit",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 edit 已完成。路径：frontend/src/App.tsx。",
            occurred_at="2026-08-10T09:44:00Z",
        ),
        _event(
            "evt-commit-ui",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：完成前端列表页面。",
            occurred_at="2026-08-10T17:45:00+08:00",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    candidate = bundle.candidates[0]
    assert candidate.node_type == NodeType.FOLLOW_UP
    assert candidate.basis_event_ids == ("evt-trace-edit", "evt-commit-ui")
    assert candidate.uncertainty.startswith("高：")


class ManyCandidateInferencer:
    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        commit = events[0]
        return tuple(
            CandidateDraft(
                node_type=NodeType.ADJUST_CONSTRAINTS,
                statement=f"候选 {index}",
                basis_event_ids=(commit.id,),
                uncertainty="高：仅用于验证候选总量边界。",
                question_to_student=f"是否确认候选 {index}？",
            )
            for index in range(MAX_CANDIDATES + 10)
        )


def test_build_archive_bundle_enforces_candidate_limit() -> None:
    event = _event("evt-cap-commit", EventKind.GIT_COMMIT, "提交 a1b2c3d：调整实现。")

    bundle = build_archive_bundle((event,), inferencer=ManyCandidateInferencer())

    assert len(bundle.candidates) == MAX_CANDIDATES
    assert bundle.warnings[0].code == "candidate_limit_applied"
    assert "省略 10 条" in bundle.warnings[0].message
