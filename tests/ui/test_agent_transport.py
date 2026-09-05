# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from learntrace.ui.embedded_runtime import AgentTransportError, EmbeddedAgentRuntime
from learntrace.ui.events import EventBroker
from learntrace.ui.storage import UIStore

SERVICE = r"""
const lines = require('node:readline').createInterface({input:process.stdin});
lines.on('line', raw => {
  const cmd = JSON.parse(raw);
  if (cmd.type === 'close') { process.exit(0); }
  if (cmd.type === 'oversize') { process.stdout.write('x'.repeat(1100000)+'\n'); return; }
  if (cmd.type === 'invalid') { process.stdout.write('not-json\n'); return; }
  if (cmd.type === 'eof') { process.exit(7); }
  if (cmd.type === 'large') {
    console.log(JSON.stringify({type:'event', event:{
      type:'message_start',message:{role:'assistant'}}}));
    console.log(JSON.stringify({type:'event', event:{type:'message_update',
      assistantMessageEvent:{type:'text_delta',delta:'中文'.repeat(30000)}}}));
  }
  console.log(JSON.stringify({type:'response',id:cmd.id,success:true}));
});
setInterval(()=>{},1000);
"""


@pytest.mark.parametrize("failure", ["oversize", "invalid", "eof"])
def test_large_events_and_broken_channels(tmp_path: Path, failure: str) -> None:
    service = tmp_path / "service.cjs"
    service.write_text(SERVICE, encoding="utf-8")
    store = UIStore(tmp_path / "ui.sqlite3")
    store.create_session({"id": "transport", "project": str(tmp_path)})
    runtime = EmbeddedAgentRuntime(
        "transport", tmp_path, tmp_path, service, tmp_path / "pi", store, EventBroker(store)
    )

    async def exercise() -> None:
        try:
            await runtime.start({})
            assert runtime.is_healthy
            # Larger than the old default 64 KiB line limit, but a valid frame.
            await asyncio.wait_for(runtime._command("large"), 5)
            assert runtime.is_healthy
            store.create_request("transport", "pending", "user_input", {"question": "test"})
            with pytest.raises(AgentTransportError):
                await asyncio.wait_for(runtime._command(failure), 5)
            assert runtime._reader_task is not None
            await asyncio.wait_for(runtime._reader_task, 5)
            assert not runtime.is_healthy
            assert runtime._process is not None and runtime._process.returncode is not None
            assert not runtime._pending
            assert store.pending_requests("transport") == []
            record = store.get_session("transport")
            assert record is not None and record["error_code"] == "agent_transport_failed"
            assert "意外退出" not in record["error_message"]
        finally:
            await runtime.close()

    try:
        asyncio.run(exercise())
    finally:
        store.close()
