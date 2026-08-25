from __future__ import annotations

from collections.abc import Callable

import pytest

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.reporting.llm import LLM_API_KEY_ENV, LLM_BASE_URL_ENV, LLM_MODEL_ENV


@pytest.fixture(autouse=True)
def clear_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (LLM_API_KEY_ENV, LLM_BASE_URL_ENV, LLM_MODEL_ENV):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def make_event() -> Callable[..., ObservableEvent]:
    """Build an ``ObservableEvent`` for tests that need no project on disk.

    Shared by the evidence-layer tests so no test module imports another test
    module (that import only worked while the repo root happened to be on
    ``sys.path``, and breaks collection on a clean checkout).
    """

    def _make(
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

    return _make
