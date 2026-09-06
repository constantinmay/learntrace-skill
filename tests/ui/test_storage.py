from __future__ import annotations

from pathlib import Path

from learntrace.ui.storage import UIStore


def _store(tmp_path: Path) -> UIStore:
    store = UIStore(tmp_path / "ui.sqlite3")
    store.create_session({"id": "session", "project": str(tmp_path)})
    return store


def test_event_sequences_survive_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.append_event("session", "message_delta", {"text": "a"})
    second = store.append_event("session", "message_delta", {"text": "b"})
    assert (first["sequence"], second["sequence"]) == (1, 2)
    assert store.events_after("session", 1) == [second]


def test_sessions_can_be_scoped_to_one_project(tmp_path: Path) -> None:
    store = UIStore(tmp_path / "ui.sqlite3")
    store.create_session({"id": "one", "project": "/one"})
    store.create_session({"id": "two", "project": "/two"})
    assert [item["id"] for item in store.list_sessions("/one")] == ["one"]


def test_answer_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.create_request("session", "question", "user_input", {"question": "Why?"})
    assert store.answer_request("session", "question", "because")
    assert not store.answer_request("session", "question", "duplicate")
