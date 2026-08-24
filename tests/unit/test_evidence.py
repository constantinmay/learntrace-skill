# pyright: reportPrivateUsage=false
from __future__ import annotations

from learntrace.evidence.chain import GapReason, build_log, tier_for
from learntrace.evidence.checkpoint import Checkpoint
from learntrace.models import (
    EventKind,
    ObservableEvent,
    SourceRef,
    SourceType,
)


def _event(
    record_id: str,
    kind: EventKind,
    summary: str,
    *,
    occurred_at: str | None = "2026-08-01T10:00:00+00:00",
) -> ObservableEvent:
    type_map = {
        EventKind.GIT_COMMIT: SourceType.GIT_COMMIT,
        EventKind.DOCUMENT: SourceType.DOCUMENT,
        EventKind.TEST_LOG: SourceType.TEST_LOG,
        EventKind.TRACE_RECORD: SourceType.TRACE_RECORD,
    }
    return ObservableEvent(
        id=record_id,
        kind=kind,
        summary=summary,
        source_refs=(SourceRef(type_map[kind], record_id),),
        occurred_at=occurred_at,
    )


def test_tier_mapping() -> None:
    assert tier_for(EventKind.GIT_COMMIT).value == "surface"
    assert tier_for(EventKind.DOCUMENT).value == "surface"
    assert tier_for(EventKind.TEST_LOG).value == "story"
    assert tier_for(EventKind.TRACE_RECORD).value == "story"


def test_log_assigns_seq_and_content_hash() -> None:
    events = (
        _event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        _event("evt-b", EventKind.TEST_LOG, "测试通过"),
    )
    log = build_log(events)
    assert [entry.seq for entry in log] == [0, 1]
    assert all(entry.content_hash for entry in log)
    assert log[0].tier.value == "surface"
    assert log[1].tier.value == "story"


def test_log_orders_by_timestamp_then_id() -> None:
    early = _event("evt-early", EventKind.GIT_COMMIT, "早", occurred_at="2026-08-01T08:00:00+00:00")
    late = _event("evt-late", EventKind.GIT_COMMIT, "晚", occurred_at="2026-08-01T09:00:00+00:00")
    log = build_log((late, early))
    assert [entry.event.id for entry in log] == ["evt-early", "evt-late"]


def test_checkpoint_capture_is_deterministic_and_binds_seq() -> None:
    events = (
        _event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        _event("evt-b", EventKind.TEST_LOG, "测试通过"),
    )
    one = Checkpoint.capture(seq_after=2, events=events, candidates=(), confirmations=())
    two = Checkpoint.capture(seq_after=2, events=events, candidates=(), confirmations=())
    assert one.content_hash == two.content_hash
    assert one.matches(two)
    assert one.seq_after == 2


def test_checkpoint_differs_on_different_state() -> None:
    a = (_event("evt-a", EventKind.GIT_COMMIT, "提交 a"),)
    b = (
        _event("evt-a", EventKind.GIT_COMMIT, "提交 a"),
        _event("evt-b", EventKind.TEST_LOG, "测试"),
    )
    one = Checkpoint.capture(seq_after=1, events=a, candidates=(), confirmations=())
    two = Checkpoint.capture(seq_after=2, events=b, candidates=(), confirmations=())
    assert not one.matches(two)


def test_gap_reason_enum_values() -> None:
    assert GapReason.MISSING_SOURCE.value == "missing_source"
    assert GapReason.MISSING_TIMESTAMPS.value == "missing_timestamps"
