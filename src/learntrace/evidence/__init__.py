"""Optional evidence-state frontend for the local archive.

This subpackage is entirely *additive*: default behaviour is unchanged. It
becomes reachable only when ``write_learning_record*`` is called with
``evidence_layer=True``.
"""

from __future__ import annotations

from learntrace.evidence.chain import EvidenceTier, LogGap
from learntrace.evidence.checkpoint import Checkpoint
from learntrace.evidence.state import EvidenceGap, EvidenceState

__all__ = [
    "EvidenceState",
    "EvidenceGap",
    "Checkpoint",
    "LogGap",
    "EvidenceTier",
]
