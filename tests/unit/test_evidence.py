from __future__ import annotations

from collections.abc import Callable

from learntrace.evidence.chain import GapReason, build_log, tier_for
from learntrace.evidence.snapshot import EvidenceSnapshot
from learntrace.models import (
    EventKind,
    ObservableEvent,
)


def test_tier_mapping() -> None:
    assert tier_for(EventKind.GIT_COMMIT).value == "surface"
    assert tier_for(EventKind.DOCUMENT).value == "surface"
    assert tier_for(EventKind.TEST_LOG).value == "story"
    assert tier_for(EventKind.TRACE_RECORD).value == "story"


def test_log_assigns_seq_and_content_hash(make_event: Callable[..., ObservableEvent]) -> None:
    events = (
        make_event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        make_event("evt-b", EventKind.TEST_LOG, "测试通过"),
    )
    log = build_log(events)
    assert [entry.seq for entry in log] == [0, 1]
    assert all(entry.content_hash for entry in log)
    assert log[0].tier.value == "surface"
    assert log[1].tier.value == "story"


def test_log_orders_by_timestamp_then_id(make_event: Callable[..., ObservableEvent]) -> None:
    early = make_event(
        "evt-early", EventKind.GIT_COMMIT, "早", occurred_at="2026-08-01T08:00:00+00:00"
    )
    late = make_event(
        "evt-late", EventKind.GIT_COMMIT, "晚", occurred_at="2026-08-01T09:00:00+00:00"
    )
    log = build_log((late, early))
    assert [entry.event.id for entry in log] == ["evt-early", "evt-late"]


def test_snapshot_capture_is_deterministic_and_binds_seq(
    make_event: Callable[..., ObservableEvent],
) -> None:
    events = (
        make_event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        make_event("evt-b", EventKind.TEST_LOG, "测试通过"),
    )
    one = EvidenceSnapshot.capture(seq_after=2, events=events, candidates=(), confirmations=())
    two = EvidenceSnapshot.capture(seq_after=2, events=events, candidates=(), confirmations=())
    assert one.content_hash == two.content_hash
    assert one.matches(two)
    assert one.seq_after == 2


def test_snapshot_differs_on_different_state(make_event: Callable[..., ObservableEvent]) -> None:
    a = (make_event("evt-a", EventKind.GIT_COMMIT, "提交 a"),)
    b = (
        make_event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        make_event("evt-b", EventKind.TEST_LOG, "测试"),
    )
    one = EvidenceSnapshot.capture(seq_after=1, events=a, candidates=(), confirmations=())
    two = EvidenceSnapshot.capture(seq_after=2, events=b, candidates=(), confirmations=())
    assert not one.matches(two)


def test_gap_reason_enum_values() -> None:
    assert GapReason.MISSING_SOURCE.value == "missing_source"
    assert GapReason.MISSING_TIMESTAMPS.value == "missing_timestamps"
