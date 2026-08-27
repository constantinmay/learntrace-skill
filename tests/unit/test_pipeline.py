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
from learntrace.reporting import (
    CandidateDraft,
    apply_confirmations,
    build_archive_bundle,
    render_questions_markdown,
)
from learntrace.reporting.pipeline import (
    MAX_CANDIDATES,
    StubCandidateInferencer,
    TemporalPlausibility,
    _temporally_plausible,
    archive_manifest,
    bundle_to_dict,
    stable_candidate_id,
    time_proximity_reflection_questions,
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


class CandidateSetInferencer:
    def __init__(self, include_extra: bool) -> None:
        self._include_extra = include_extra

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        commit = events[0]
        original = CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能修复了输入解析问题。",
            basis_event_ids=(commit.id,),
            uncertainty="高：只有提交记录。",
            question_to_student="这次修改是否用于修复解析问题？",
        )
        extra = CandidateDraft(
            node_type=NodeType.FIX_FAILED_APPROACH,
            statement="学生可能同时整理了错误处理分支。",
            basis_event_ids=(commit.id,),
            uncertainty="高：只有提交记录。",
            question_to_student="是否同时整理了错误处理？",
        )
        return (original, extra) if self._include_extra else (original,)


def test_candidate_id_does_not_change_when_unrelated_candidate_is_added() -> None:
    events = (_event("evt-set-1", EventKind.GIT_COMMIT, "提交 a1b2c3d：重写解析逻辑。"),)

    single = build_archive_bundle(events, inferencer=CandidateSetInferencer(False))
    expanded = build_archive_bundle(events, inferencer=CandidateSetInferencer(True))

    assert single.candidates[0].id == expanded.candidates[0].id


def test_candidate_id_is_independent_of_basis_event_order() -> None:
    first = CandidateDraft(
        node_type=NodeType.REVISE_AI_SUGGESTION,
        statement="学生可能调整了建议。",
        basis_event_ids=("evt-z-commit", "evt-a-trace"),
        uncertainty="高：需要确认。",
        question_to_student="是否调整了建议？",
    )
    reversed_basis = CandidateDraft(
        node_type=first.node_type,
        statement=first.statement,
        basis_event_ids=tuple(reversed(first.basis_event_ids)),
        uncertainty=first.uncertainty,
        question_to_student=first.question_to_student,
    )

    assert stable_candidate_id(first) == stable_candidate_id(reversed_basis)


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


def test_identical_candidates_are_deduplicated_without_order_suffixes() -> None:
    """Repeated questions become one stable candidate, never order suffixes."""
    events = (_event("evt-sy-1", EventKind.GIT_COMMIT, "提交 a1b2c3d：重写。"),)

    bundle = build_archive_bundle(events, inferencer=IdenticalCandidateInferencer())

    assert len(bundle.candidates) == 1
    assert [warning.code for warning in bundle.warnings] == ["duplicate_candidates_removed"]


class SameQuestionDifferentBasisInferencer:
    def __init__(self, *, reverse: bool = False) -> None:
        self._reverse = reverse

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        drafts = tuple(
            CandidateDraft(
                node_type=NodeType.FIX_FAILED_APPROACH,
                statement="同一问题可能经过了两次修复尝试。",
                basis_event_ids=(event.id,),
                uncertainty="中：两条提交都与同一问题有关。",
                question_to_student="这两次修改是否属于同一次修复过程？",
            )
            for event in events
        )
        return tuple(reversed(drafts)) if self._reverse else drafts


def test_duplicate_candidate_drafts_merge_basis_deterministically() -> None:
    events = (
        _event("evt-merge-b", EventKind.GIT_COMMIT, "提交 b：第二次修改。"),
        _event("evt-merge-a", EventKind.GIT_COMMIT, "提交 a：第一次修改。"),
    )

    first = build_archive_bundle(events, inferencer=SameQuestionDifferentBasisInferencer())
    reversed_result = build_archive_bundle(
        events,
        inferencer=SameQuestionDifferentBasisInferencer(reverse=True),
    )

    assert len(first.candidates) == 1
    assert first.candidates[0].basis_event_ids == ("evt-merge-a", "evt-merge-b")
    assert first.candidates[0].id == reversed_result.candidates[0].id
    assert [warning.code for warning in first.warnings] == ["duplicate_candidates_removed"]


def test_custom_inferencer_failure_is_not_swallowed() -> None:
    class FailingInferencer:
        inference_mode = "llm"

        def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
            raise RuntimeError("custom failure")

    event = _event("evt-custom", EventKind.GIT_COMMIT, "提交 a：修改实现。")

    with pytest.raises(RuntimeError, match="custom failure"):
        build_archive_bundle((event,), inferencer=FailingInferencer())


def test_default_inferencer_is_stub_without_llm_environment() -> None:
    event = _event("evt-default", EventKind.GIT_COMMIT, "提交 a：修改实现。")

    bundle = build_archive_bundle((event,))

    assert bundle.inference_mode == "stub"


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


def test_denied_confirmation_resolves_question_without_confirming_candidate() -> None:
    commit = _commit("evt-denied", "2026-05-01T10:00:00+08:00")
    inferencer = SingleCommitInferencer()
    initial = build_archive_bundle((commit,), inferencer=inferencer)
    confirmation = _confirmation(
        "conf-denied",
        initial.candidates[0].id,
        ConfirmationDecision.DENIED,
    )

    updated = apply_confirmations(initial, (confirmation,))

    assert updated.candidates[0].status.value == "resolved"
    assert updated.confirmations[0].decision is ConfirmationDecision.DENIED


def test_apply_confirmations_rejects_unknown_candidate_id() -> None:
    commit = _commit("evt-known", "2026-05-01T10:00:00+08:00")
    initial = build_archive_bundle((commit,), inferencer=SingleCommitInferencer())
    confirmation = _confirmation(
        "conf-unknown",
        "cand-no-longer-present",
        ConfirmationDecision.CONFIRMED,
    )

    with pytest.raises(ValueError, match="confirmation targets missing candidate"):
        apply_confirmations(initial, (confirmation,))


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


def test_stub_does_not_infer_learning_from_generic_tool_completion() -> None:
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

    assert bundle.candidates == ()


def test_low_signal_trace_batch_does_not_create_follow_up_flood() -> None:
    traces = tuple(
        _event(
            f"evt-noise-{index}",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 bash 已完成。命令类型：git log。",
            occurred_at=f"2026-08-10T09:{index:02d}:00Z",
        )
        for index in range(12)
    )
    relevant = _event(
        "evt-relevant",
        EventKind.TRACE_RECORD,
        "OpenCode 工具 edit 已完成。路径：frontend/src/App.tsx。",
        occurred_at="2026-08-10T09:12:30Z",
    )
    commits = tuple(
        _event(
            f"evt-ui-commit-{index}",
            EventKind.GIT_COMMIT,
            f"提交 a1b2c{index:x}：完成前端页面模块 {index}。",
            occurred_at=f"2026-08-10T09:{13 + index:02d}:00Z",
        )
        for index in range(12)
    )

    bundle = build_archive_bundle(
        (*traces, relevant, *commits), inferencer=StubCandidateInferencer()
    )

    assert bundle.candidates == ()


def test_generic_write_trace_does_not_become_ai_revision() -> None:
    events = (
        _event(
            "evt-trace-write",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 write 已完成。路径：.gitignore。",
            occurred_at="2026-08-10T09:40:00Z",
        ),
        _event(
            "evt-commit-ignore",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：将编译产物加入 .gitignore 并从版本库移除。",
            occurred_at="2026-08-10T09:45:00Z",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert bundle.candidates == ()


def test_fix_commit_without_test_log_yields_conservative_question() -> None:
    events = (
        _event(
            "evt-fix-commit",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：fix: 修复 created_at 时间格式不一致。",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    candidate = bundle.candidates[0]
    assert candidate.node_type == NodeType.FIX_FAILED_APPROACH
    assert candidate.basis_event_ids == ("evt-fix-commit",)
    assert candidate.uncertainty.startswith("高：")
    assert isinstance(candidate.question_to_student, str)
    assert candidate.question_to_student.strip()


def test_python_error_name_matches_synonymous_chinese_fix_commit() -> None:
    events = (
        _event(
            "evt-zero-division-log",
            EventKind.TEST_LOG,
            "测试运行失败：test_average 空输入触发 ZeroDivisionError。",
        ),
        _event(
            "evt-zero-division-fix",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：修复空输入时的除零错误。",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type == NodeType.FIX_FAILED_APPROACH
    assert bundle.candidates[0].basis_event_ids == (
        "evt-zero-division-log",
        "evt-zero-division-fix",
    )


def _session_trace(
    record_id: str,
    session_id: str,
    summary: str,
    occurred_at: str,
) -> ObservableEvent:
    return ObservableEvent(
        id=record_id,
        kind=EventKind.TRACE_RECORD,
        summary=summary,
        source_refs=(
            SourceRef(
                type=SourceType.TRACE_RECORD,
                ref=f"trace://opencode/{session_id}/message/msg/part/{record_id}",
            ),
        ),
        occurred_at=occurred_at,
    )


def test_stub_can_connect_topically_related_distinct_opencode_sessions() -> None:
    events = (
        _session_trace(
            "evt-session-one",
            "ses_one",
            "OpenCode 工具 edit 已完成。路径：src/parser.py。",
            "2026-08-10T09:00:00Z",
        ),
        _session_trace(
            "evt-session-two",
            "ses_two",
            "OpenCode 工具 bash 已完成。命令类型：pytest parser。",
            "2026-08-10T10:00:00Z",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type == NodeType.FOLLOW_UP
    assert "后续 OpenCode 会话" in bundle.candidates[0].statement
    assert bundle.candidates[0].basis_event_ids == (
        "evt-session-one",
        "evt-session-two",
    )


def test_stub_does_not_connect_unrelated_or_distant_sessions() -> None:
    unrelated = (
        _session_trace(
            "evt-session-parser",
            "ses_parser",
            "OpenCode 工具 edit 已完成。路径：src/parser.py。",
            "2026-08-10T09:00:00Z",
        ),
        _session_trace(
            "evt-session-ui",
            "ses_ui",
            "OpenCode 工具 edit 已完成。路径：frontend/theme.css。",
            "2026-08-10T10:00:00Z",
        ),
    )
    distant = (
        unrelated[0],
        _session_trace(
            "evt-session-parser-later",
            "ses_parser_later",
            "OpenCode 工具 bash 已完成。命令类型：pytest parser。",
            "2026-08-12T10:00:00Z",
        ),
    )

    assert build_archive_bundle(unrelated, inferencer=StubCandidateInferencer()).candidates == ()
    assert build_archive_bundle(distant, inferencer=StubCandidateInferencer()).candidates == ()


def test_stub_does_not_treat_repeated_read_in_different_sessions_as_follow_up() -> None:
    events = (
        _session_trace(
            "evt-read-one",
            "ses_one",
            "OpenCode 工具 read 已完成。路径：README.md。",
            "2026-08-10T09:00:00Z",
        ),
        _session_trace(
            "evt-read-two",
            "ses_two",
            "OpenCode 工具 read 已完成。路径：README.md。",
            "2026-08-10T10:00:00Z",
        ),
    )

    assert build_archive_bundle(events, inferencer=StubCandidateInferencer()).candidates == ()


def test_stub_rejects_low_signal_and_topically_unrelated_nearby_traces() -> None:
    events = (
        _event(
            "evt-trace-ls",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 bash 已完成。命令类型：ls。",
            occurred_at="2026-08-10T09:40:00Z",
        ),
        _event(
            "evt-trace-backend",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 write 已完成。路径：backend/api.py。",
            occurred_at="2026-08-10T09:44:00Z",
        ),
        _event(
            "evt-commit-ui",
            EventKind.GIT_COMMIT,
            "提交 a1b2c3d：完成前端列表页面。",
            occurred_at="2026-08-10T09:45:00Z",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert bundle.candidates == ()


@pytest.mark.parametrize(
    ("trace_summary", "commit_summary"),
    [
        (
            "AI 提议使用模式匹配检查用户编号格式。",
            "提交 a1b2c3d：替换为逐字符数字检查并增加长度限制。",
        ),
        (
            "AI suggested automatic column inference when loading tabular data.",
            "Commit a1b2c3d: replace inference with declared column types.",
        ),
    ],
)
def test_revision_heuristic_accepts_synonymous_wording(
    trace_summary: str,
    commit_summary: str,
) -> None:
    events = (
        _event("evt-trace", EventKind.TRACE_RECORD, trace_summary),
        _event("evt-commit", EventKind.GIT_COMMIT, commit_summary),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type == NodeType.REVISE_AI_SUGGESTION


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


def test_time_proximity_nearby_trace_commit_no_longer_creates_candidate() -> None:
    """Issue #29: a learning-signal trace within 30 min of a topical commit
    must not be materialized as a follow_up candidate; the pair may only
    surface as a student reflection question."""
    events = (
        _event(
            "evt-trace-parse",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 write 已完成，学生决定先修复 CSV 解析失败再提交。路径：parser.py。",
            occurred_at="2026-08-10T09:40:00Z",
        ),
        _event(
            "evt-commit-parse",
            EventKind.GIT_COMMIT,
            '提交 a1b2c3d 的提交信息为"调整解析逻辑"，记录 1 个文件变更（1 个修改）。',
            occurred_at="2026-08-10T09:45:00Z",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert bundle.candidates == ()

    archive = bundle_to_dict(bundle)
    pending = cast(list[dict[str, object]], archive["pending_questions"])
    proximity = [
        question for question in pending if question["question_type"] == "time_proximity_review"
    ]
    assert len(proximity) == 1
    assert proximity[0]["candidate_id"] is None
    assert proximity[0]["basis_event_ids"] == ["evt-trace-parse", "evt-commit-parse"]
    assert "proximity-evt-trace-parse-evt-commit-parse" in render_questions_markdown(bundle)
    assert "未确认" in render_questions_markdown(bundle)


def test_generic_nearby_trace_commit_produces_no_proximity_question() -> None:
    """A trace without a learning signal stays invisible: neither a candidate
    nor a proximity question."""
    events = (
        _event(
            "evt-trace-write",
            EventKind.TRACE_RECORD,
            "OpenCode 工具 write 已完成。路径：parser.py。",
            occurred_at="2026-08-10T09:40:00Z",
        ),
        _event(
            "evt-commit-parse",
            EventKind.GIT_COMMIT,
            '提交 a1b2c3d 的提交信息为"调整解析逻辑"，记录 1 个文件变更（1 个修改）。',
            occurred_at="2026-08-10T09:45:00Z",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=StubCandidateInferencer())

    assert bundle.candidates == ()
    assert time_proximity_reflection_questions(bundle) == ()
    archive = bundle_to_dict(bundle)
    pending = cast(list[dict[str, object]], archive["pending_questions"])
    assert all(question["question_type"] != "time_proximity_review" for question in pending)
    assert "proximity-" not in render_questions_markdown(bundle)
