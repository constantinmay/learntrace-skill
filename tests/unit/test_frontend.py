# pyright: reportPrivateUsage=false
from __future__ import annotations

from learntrace.evidence.frontend import build_evidence_state
from learntrace.models import (
    EventKind,
)
from learntrace.reporting import ArchiveBundle
from tests.unit.test_evidence import _event


def test_frontend_detects_missing_timestamp_gap() -> None:
    events = (
        _event("evt-a", EventKind.GIT_COMMIT, "提交 a", occurred_at="2026-08-01T08:00:00+00:00"),
        _event("evt-b", EventKind.TRACE_RECORD, "轨迹", occurred_at=None),
    )
    bundle = ArchiveBundle(events=events, candidates=(), confirmations=(), inference_mode="stub")
    state, checkpoint = build_evidence_state(bundle)
    keys = {gap.key for gap in state.gaps}
    assert "timestamps" in keys
    assert checkpoint.seq_after == 2


def test_frontend_detects_missing_source_gaps() -> None:
    # Only document evidence: no test log -> missing test-evidence gap.
    events = (
        _event("evt-a", EventKind.DOCUMENT, "任务书", occurred_at="2026-08-01T08:00:00+00:00"),
    )
    bundle = ArchiveBundle(events=events, candidates=(), confirmations=(), inference_mode="stub")
    state, _ = build_evidence_state(bundle)
    keys = {gap.key for gap in state.gaps}
    assert "test-evidence" in keys
