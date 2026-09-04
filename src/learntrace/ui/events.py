"""Persistent event broker used by REST replay and live SSE subscribers."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from learntrace.ui.storage import UIStore


class EventBroker:
    def __init__(self, store: UIStore) -> None:
        self.store = store
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    async def publish(self, session_id: str, event_type: str, payload: Any) -> dict[str, Any]:
        event = self.store.append_event(session_id, event_type, payload)
        for queue in tuple(self._subscribers[session_id]):
            queue.put_nowait(event)
        return event

    async def subscribe(self, session_id: str, after: int = 0) -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for event in self.store.events_after(session_id, after):
            queue.put_nowait(event)
        self._subscribers[session_id].add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {event['sequence']}\nevent: {event['type']}\ndata: {data}\n\n"
        finally:
            self._subscribers[session_id].discard(queue)
