"""Glue from the archive bundle to the optional evidence-state frontend.

The archive pipeline already produces an ``ArchiveBundle`` (events, candidates,
confirmations, warnings). This module turns it into an ``EvidenceState`` with an
explicit gap register, and captures a ``Checkpoint`` bound to the event-log
position the bundle was taken after.

Nothing here changes how the bundle is built; it only derives the additive
view. Consumers that do not opt in never call this module.
"""

from __future__ import annotations

from learntrace.evidence.chain import GapReason, LogGap, build_log
from learntrace.evidence.checkpoint import Checkpoint
from learntrace.evidence.state import EvidenceGap, EvidenceState
from learntrace.models import EventKind
from learntrace.reporting import ArchiveBundle


def build_evidence_state(bundle: ArchiveBundle) -> tuple[EvidenceState, Checkpoint]:
    """Derive the audit state and its checkpoint from a validated bundle."""

    log = build_log(bundle.events)
    seq_after = len(log)

    gaps = _detect_gaps(bundle)
    state = EvidenceState(
        events=bundle.events,
        candidates=bundle.candidates,
        confirmations=bundle.confirmations,
        gaps=gaps,
    )
    checkpoint = Checkpoint.capture(
        seq_after=seq_after,
        events=bundle.events,
        candidates=bundle.candidates,
        confirmations=bundle.confirmations,
    )
    return state, checkpoint


def _detect_gaps(bundle: ArchiveBundle) -> tuple[EvidenceGap, ...]:
    gaps: list[EvidenceGap] = []

    kinds = {event.kind for event in bundle.events}
    if EventKind.TEST_LOG not in kinds:
        gaps.append(
            _gap(
                key="test-evidence",
                label="测试运行记录",
                reason="missing_source",
                message="没有发现测试运行记录；是否运行过测试无法从现有证据判断。",
            )
        )
    if EventKind.DOCUMENT not in kinds:
        gaps.append(
            _gap(
                key="goal-evidence",
                label="项目目标记录",
                reason="missing_source",
                message="没有明确记录项目目标；缺少可引用的任务书或要求类文档。",
            )
        )
    if any(not event.occurred_at for event in bundle.events):
        gaps.append(
            _gap(
                key="timestamps",
                label="事件时间戳",
                reason="missing_timestamps",
                message="部分事件缺少时间戳，其间顺序无法由系统确认。",
            )
        )
    return tuple(gaps)


def _gap(*, key: str, label: str, reason: str, message: str) -> EvidenceGap:
    return EvidenceGap(
        key=key,
        label=label,
        detail=LogGap(reason=GapReason(reason), message=message),
    )
