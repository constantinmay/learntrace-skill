"""State/projection separation: the audit view and the register of missing bits.

LearnTrace's existing ``learning-record.md`` is a *projection*: a student- and
teacher-readable excerpt that shows only selected facts. The audit state — the
full evidence log and its account of what was witnessed — should live as a
separate *state* object so that "what did we see, in full" is never conflated
with "what do we show".

This module provides the state object (``EvidenceState``) and the marker of
missing evidence (``EvidenceGap``), so a gap is an explicit, queryable part of
the state, not a silent hole.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from learntrace.evidence.chain import LogGap
from learntrace.models import (
    LearningNodeCandidate,
    ObservableEvent,
    StudentConfirmation,
)


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    """A named stretch of evidence the state knows is missing."""

    key: str
    label: str
    detail: LogGap

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "detail": self.detail.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceState:
    """The audit state: everything stored as structured evidence, plus gaps.

    This is deliberately richer than the Markdown projection. The projection
    answers "what should the student/teacher read"; this object answers "what
    does the archive actually know, and what does it know it does not know".
    """

    events: tuple[ObservableEvent, ...] = ()
    candidates: tuple[LearningNodeCandidate, ...] = ()
    confirmations: tuple[StudentConfirmation, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_counts": {
                "observable_fact": len(self.events),
                "candidate_inference": len(self.candidates),
                "student_confirmation": len(self.confirmations),
            },
            "gaps": [gap.to_dict() for gap in self.gaps],
        }
