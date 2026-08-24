"""Checkpoints: immutable state snapshots bound to a log position and digest.

A checkpoint pins an exact view of the learning archive — which events, which
candidates, which confirmations — to the event-log ``seq`` it was taken after
and a content hash. Two checkpoints whose hash differs are provably different
states; no amount of re-reading can turn one into the other silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from learntrace.models import (
    LearningNodeCandidate,
    ObservableEvent,
    StudentConfirmation,
)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """An immutable snapshot of archive state at a moment in time."""

    seq_after: int
    events: tuple[ObservableEvent, ...]
    candidates: tuple[LearningNodeCandidate, ...]
    confirmations: tuple[StudentConfirmation, ...]
    content_hash: str

    @classmethod
    def capture(
        cls,
        *,
        seq_after: int,
        events: tuple[ObservableEvent, ...],
        candidates: tuple[LearningNodeCandidate, ...],
        confirmations: tuple[StudentConfirmation, ...],
    ) -> Checkpoint:
        import hashlib
        import json

        record = {
            "seq_after": seq_after,
            "events": [event.to_dict() for event in events],
            "candidates": [candidate.to_dict() for candidate in candidates],
            "confirmations": [confirmation.to_dict() for confirmation in confirmations],
        }
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return cls(
            seq_after=seq_after,
            events=events,
            candidates=candidates,
            confirmations=confirmations,
            content_hash=content_hash,
        )

    def matches(self, other: Checkpoint) -> bool:
        return self.content_hash == other.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq_after": self.seq_after,
            "content_hash": self.content_hash,
            "record_counts": {
                "observable_fact": len(self.events),
                "candidate_inference": len(self.candidates),
                "student_confirmation": len(self.confirmations),
            },
        }
