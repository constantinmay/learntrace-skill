"""SQLite persistence for Pi sessions, visible events, sources, and artifacts."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class UIStore:
    """Synchronized local store. Secrets and hidden reasoning never enter it."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                  id TEXT PRIMARY KEY, project TEXT NOT NULL,
                  title TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, error_code TEXT, error_message TEXT);
                CREATE TABLE IF NOT EXISTS events (
                  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  sequence INTEGER NOT NULL, created_at TEXT NOT NULL, type TEXT NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY(session_id, sequence));
                CREATE TABLE IF NOT EXISTS requests (
                  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  request_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                  state TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL,
                  answered_at TEXT, PRIMARY KEY(session_id, request_id));
                CREATE TABLE IF NOT EXISTS artifacts (
                  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  path TEXT NOT NULL, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                  modified_ns INTEGER NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL,
                  details TEXT NOT NULL, PRIMARY KEY(session_id, path));
                CREATE TABLE IF NOT EXISTS conversation_sources (
                  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                  path TEXT NOT NULL, source_type TEXT NOT NULL,
                  authorization TEXT NOT NULL, state TEXT NOT NULL,
                  size INTEGER NOT NULL, created_at TEXT NOT NULL,
                  PRIMARY KEY(session_id, path));
                """
            )

    def create_session(self, record: Mapping[str, Any]) -> None:
        now = utc_now()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions"
                "(id,project,title,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    record["id"],
                    record["project"],
                    record.get("title", "LearnTrace 分析"),
                    record.get("state", "created"),
                    now,
                    now,
                ),
            )

    def update_session(self, session_id: str, **changes: Any) -> None:
        allowed = {
            "title",
            "state",
            "error_code",
            "error_message",
        }
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE sessions SET {assignments} WHERE id=?", (*values.values(), session_id)
            )  # noqa: S608

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, project: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if project is None:
                rows = self._db.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM sessions WHERE project=? ORDER BY updated_at DESC",
                    (project,),
                ).fetchall()
        return [dict(row) for row in rows]

    def append_event(self, session_id: str, kind: str, payload: Any) -> dict[str, Any]:
        created = utc_now()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE session_id=?", (session_id,)
            ).fetchone()
            sequence = int(row[0])
            self._db.execute(
                "INSERT INTO events VALUES(?,?,?,?,?)",
                (session_id, sequence, created, kind, encoded),
            )
        return {
            "session_id": session_id,
            "sequence": sequence,
            "created_at": created,
            "type": kind,
            "payload": payload,
        }

    def events_after(self, session_id: str, sequence: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM events WHERE session_id=? AND sequence>? ORDER BY sequence",
                (session_id, sequence),
            ).fetchall()
        return [
            {
                "session_id": row["session_id"],
                "sequence": row["sequence"],
                "created_at": row["created_at"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def has_event(self, session_id: str, kind: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM events WHERE session_id=? AND type=? LIMIT 1",
                (session_id, kind),
            ).fetchone()
        return row is not None

    def create_request(
        self, session_id: str, request_id: str, kind: str, payload: Mapping[str, Any]
    ) -> bool:
        with self._lock, self._db:
            result = self._db.execute(
                "INSERT OR IGNORE INTO requests"
                "(session_id,request_id,kind,payload,state,created_at) "
                "VALUES(?,?,?,?,'pending',?)",
                (session_id, request_id, kind, json.dumps(payload, ensure_ascii=False), utc_now()),
            )
        return result.rowcount == 1

    def answer_request(self, session_id: str, request_id: str, response: Any) -> bool:
        with self._lock, self._db:
            result = self._db.execute(
                "UPDATE requests SET state='answered',response=?,answered_at=? "
                "WHERE session_id=? AND request_id=? AND state='pending'",
                (json.dumps(response, ensure_ascii=False), utc_now(), session_id, request_id),
            )
        return result.rowcount == 1

    def get_request(self, session_id: str, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM requests WHERE session_id=? AND request_id=?",
                (session_id, request_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["response"] = json.loads(item["response"]) if item["response"] else None
        return item

    def pending_requests(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM requests WHERE session_id=? AND state='pending'", (session_id,)
            ).fetchall()
        return [
            {**dict(row), "payload": json.loads(row["payload"]), "response": None} for row in rows
        ]

    def expire_pending_requests(self, session_id: str) -> int:
        """Close questions whose in-memory Pi tool call no longer exists."""
        with self._lock, self._db:
            result = self._db.execute(
                "UPDATE requests SET state='interrupted',answered_at=? "
                "WHERE session_id=? AND state='pending'",
                (utc_now(), session_id),
            )
        return result.rowcount

    def upsert_artifact(self, session_id: str, item: Mapping[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(session_id,path) DO UPDATE SET
                  kind=excluded.kind,
                  fingerprint=excluded.fingerprint,
                  modified_ns=excluded.modified_ns,
                  size=excluded.size,
                  status=excluded.status,
                  details=excluded.details""",
                (
                    session_id,
                    item["path"],
                    item["kind"],
                    item["fingerprint"],
                    item["modified_ns"],
                    item["size"],
                    item["status"],
                    json.dumps(item.get("details", {}), ensure_ascii=False),
                ),
            )

    def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM artifacts WHERE session_id=? ORDER BY path", (session_id,)
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return result

    def add_conversation_source(self, session_id: str, item: Mapping[str, Any]) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO conversation_sources "
                "(session_id,path,source_type,authorization,state,size,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    session_id,
                    item["path"],
                    item["source_type"],
                    item["authorization"],
                    item.get("state", "authorized"),
                    item["size"],
                    utc_now(),
                ),
            )

    def list_conversation_sources(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT path,source_type,authorization,state,size,created_at "
                "FROM conversation_sources WHERE session_id=? ORDER BY created_at,path",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._db:
            result = self._db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        return result.rowcount == 1

    def close(self) -> None:
        with self._lock:
            self._db.close()
