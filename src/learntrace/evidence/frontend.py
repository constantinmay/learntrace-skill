"""Glue from the archive bundle to the optional evidence-state frontend.

The archive pipeline already produces an ``ArchiveBundle`` (events, candidates,
confirmations, warnings). This module turns it into an ``EvidenceState`` with
an explicit gap register, and captures an ``EvidenceSnapshot`` bound to the
event-log position the bundle was taken after.

Nothing here changes how the bundle is built; it only derives the additive
view. Consumers that do not opt in never call this module.
"""

from __future__ import annotations

from learntrace.evidence.chain import GapReason, LogGap, build_log
from learntrace.evidence.snapshot import EvidenceSnapshot
from learntrace.evidence.state import EvidenceGap, EvidenceState
from learntrace.reporting import ArchiveBundle
from learntrace.reporting.pipeline import EVIDENCE_GAP_SPECS


def build_evidence_state(bundle: ArchiveBundle) -> tuple[EvidenceState, EvidenceSnapshot]:
    """Derive the audit state and its snapshot from a validated bundle."""

    log = build_log(bundle.events)
    seq_after = len(log)

    gaps = _detect_gaps(bundle)
    state = EvidenceState(
        events=bundle.events,
        candidates=bundle.candidates,
        confirmations=bundle.confirmations,
        gaps=gaps,
    )
    snapshot = EvidenceSnapshot.capture(
        seq_after=seq_after,
        events=bundle.events,
        candidates=bundle.candidates,
        confirmations=bundle.confirmations,
    )
    return state, snapshot


def _detect_gaps(bundle: ArchiveBundle) -> tuple[EvidenceGap, ...]:
    gaps: list[EvidenceGap] = []

    kinds = {event.kind for event in bundle.events}
    for spec in EVIDENCE_GAP_SPECS:
        if spec.required_kind not in kinds:
            gaps.append(
                _gap(
                    key=spec.key,
                    label=spec.label,
                    reason=spec.reason,
                    message=spec.statement,
                )
            )
    if any(not event.occurred_at for event in bundle.events):
        # The reporting pipeline has no counterpart question for partial
        # timestamps, so this marker keeps its own wording here.
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
