"""Semantic event log: an additive, auditable view over raw observable facts.

The four ``EventKind`` values (``git_commit`` / ``document`` / ``test_log`` /
``trace_record``) are raw evidence as parsed from a project. This module
re-lays that raw evidence into a single ordered log where every entry carries:

- an explicit ``seq`` number (append order);
- a ``level`` grouping the raw kind into one of two semantic tiers
  (``surface`` for a student action already reflected in an artifact, ``story``
  for the authored-at-the-time records that witness the action);
- a stable content digest, so two logs whose entry hashes differ can never be
  mistaken for "the same" history.

Nothing here rewrites or replaces ``ObservableEvent``. The log is a projection
of already-validated records meant to make "what is the evidence, in order, and
is any of it missing" explicit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from learntrace.models import EventKind, ObservableEvent


class GapReason(StrEnum):
    """Why a stretch of history is marked missing rather than guessed."""

    MISSING_SOURCE = "missing_source"
    MISSING_TIMESTAMPS = "missing_timestamps"


@dataclass(frozen=True, slots=True)
class LogGap:
    """An explicit break in the evidence chain.

    History that is not witnessed is *not* reconstructed. It is recorded as a
    gap so that downstream readers cannot mistake an inferred ordering for an
    observed one.
    """

    reason: GapReason
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"reason": str(self.reason), "message": self.message}


class EvidenceTier(StrEnum):
    """Semantic grouping of raw event kinds.

    ``surface`` events change a project artifact directly (a commit, a
    document). ``story`` events witness the work while it happened (a test log,
    an authorized trace record).
    """

    SURFACE = "surface"
    STORY = "story"


_KIND_TO_TIER: dict[EventKind, EvidenceTier] = {
    EventKind.GIT_COMMIT: EvidenceTier.SURFACE,
    EventKind.DOCUMENT: EvidenceTier.SURFACE,
    EventKind.TEST_LOG: EvidenceTier.STORY,
    EventKind.TRACE_RECORD: EvidenceTier.STORY,
}


def tier_for(kind: EventKind) -> EvidenceTier:
    """Map a raw event kind to its semantic tier."""
    return _KIND_TO_TIER[kind]


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One node in the semantic event log."""

    seq: int
    event: ObservableEvent
    tier: EvidenceTier
    content_hash: str

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "seq": self.seq,
            "id": self.event.id,
            "kind": self.event.kind.value,
            "tier": self.tier.value,
            "content_hash": self.content_hash,
            "occurred_at": self.event.occurred_at or "",
        }
        return data


def _content_digest(value: object, algorithm: str = "sha256") -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.new(algorithm, canonical.encode("utf-8")).hexdigest()


def _order_key(event: ObservableEvent) -> tuple[str, str]:
    # Same ordering convention as the reporting pipeline: timestamp first, then
    # id. Missing timestamps sort to the front, which is exactly the "we cannot
    # prove this came after anything" case.
    return (event.occurred_at or "", event.id)


def build_log(events: tuple[ObservableEvent, ...]) -> tuple[LogEntry, ...]:
    ordered = tuple(sorted(events, key=_order_key))
    entries: list[LogEntry] = []
    for seq, event in enumerate(ordered, start=0):
        entries.append(
            LogEntry(
                seq=seq,
                event=event,
                tier=tier_for(event.kind),
                content_hash=_content_digest(event.to_dict()),
            )
        )
    return tuple(entries)
