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
