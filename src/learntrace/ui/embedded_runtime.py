"""Thin transport to the bundled Node service powered by the Pi SDK."""
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from learntrace.ui.events import EventBroker
from learntrace.ui.storage import UIStore

_MINIMUM_NODE_VERSION = (22, 19, 0)


def agent_error_details(error: Exception) -> tuple[str, str]:
    message = str(error).strip() or type(error).__name__
    lowered = message.casefold()
    if "node.js" in lowered or "pi agent service" in lowered:
        return "agent_runtime_missing", message
    if any(
        value in lowered
        for value in ("api key", "authentication", "unauthorized", "401", "403", "forbidden")
    ):
        return "agent_auth_required", f"API 配置不可用：{message}"
    if any(
        value in lowered
        for value in ("api 地址必须", "不支持的 api 协议", "invalid url", "invalid_request")
    ):
        return "agent_config_invalid", f"模型配置格式不正确：{message}"
    if any(
        value in lowered for value in ("model not found", "unknown model", "invalid model", "404")
    ):
        return "agent_model_invalid", f"模型 ID 或接口地址不可用：{message}"
    if any(
        value in lowered
        for value in (
            "rate limit",
            "429",
            "quota",
            "usage exceeded",
            "timed out",
            "timeout",
            "connection",
            "econnrefused",
            "enotfound",
            "fetch failed",
            "network",
            "502",
            "503",
            "504",
        )
    ):
        return "agent_model_unavailable", f"模型服务暂时不可用：{message}"
    return "agent_prompt_failed", message


def node_executable() -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("使用 LearnTrace UI 需要安装 Node.js 22.19 或更高版本。")
    try:
        completed = subprocess.run(
            [node, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"无法检查 Node.js 版本：{error}") from error
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", completed.stdout.strip())
    if not match:
        raise RuntimeError(f"无法识别 Node.js 版本：{completed.stdout.strip()}")
    version = tuple(int(part) for part in match.groups())
    if version < _MINIMUM_NODE_VERSION:
        found = ".".join(str(part) for part in version)
        raise RuntimeError(f"LearnTrace UI 需要 Node.js 22.19 或更高版本，当前为 {found}。")
    return node


async def probe_model(node_service: Path, config: dict[str, Any]) -> None:
    """Verify one model configuration through the same Pi SDK transport used by sessions."""
    if not node_service.is_file():
        raise RuntimeError("LearnTrace UI 的 Pi Agent 服务未包含在当前安装包中。")
    node = node_executable()
    flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
    process = await asyncio.create_subprocess_exec(
        node,
        str(node_service),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=flags,
        env=agent_environment(),
    )
    assert process.stdin and process.stdout
    request_id = secrets.token_urlsafe(10)
    payload = {
        "id": request_id,
        "type": "probe",
        "sessionId": f"probe-{secrets.token_hex(8)}",
        "config": config,
    }
    try:
        process.stdin.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        )
        await process.stdin.drain()
        raw = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        if not raw:
            stderr = await process.stderr.read() if process.stderr else b""
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail or "Pi Agent 服务在连接测试期间退出。")
        response = json.loads(raw.decode("utf-8"))
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise RuntimeError("Pi Agent 服务返回了无法识别的连接测试结果。")
        if not response.get("success", False):
            raise RuntimeError(str(response.get("error") or "模型连接测试失败。"))
    except TimeoutError as error:
        raise RuntimeError("模型连接测试超时。") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Pi Agent 服务返回了无效数据。") from error
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.terminate()
            await process.wait()


def _message_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = cast(dict[str, Any], message).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _tool_title(event: dict[str, Any]) -> str:
    name = str(event.get("toolName", "tool"))
    args = event.get("args") if isinstance(event.get("args"), dict) else {}
    args = cast(dict[str, Any], args)
    if name in {"read", "write", "edit"}:
        return f"{name} {args.get('path') or args.get('file_path') or ''}".strip()
    if name == "bash":
        return str(args.get("command", ""))[:240] or name
    if name in {"grep", "find", "ls"}:
        return f"{name} {args.get('path') or args.get('pattern') or ''}".strip()
    return name


def agent_environment(
    inherited: dict[str, str] | None = None, *, executable: str | None = None
) -> dict[str, str]:
    """Return the environment in which the bundled Agent runs project tools.

    The UI is commonly opened through an installed console entry point.  In
    that case Windows does not promise that the virtual environment's Scripts
    directory is in PATH for child Node processes.  Put the interpreter's
    command directory first while retaining every existing user setting.
    """
    environment = (inherited or os.environ).copy()
    command_dir = str(Path(executable or sys.executable).resolve().parent)
    path_key = "Path" if os.name == "nt" and "Path" in environment else "PATH"
    existing = environment.get(path_key, "")
    entries = [entry for entry in existing.split(os.pathsep) if entry]
    if command_dir not in entries:
        environment[path_key] = os.pathsep.join([command_dir, *entries])
    return environment


class EmbeddedAgentRuntime:
    def __init__(
        self,
        session_id: str,
        project: Path,
        skill_root: Path,
        node_service: Path,
        session_dir: Path,
        store: UIStore,
        broker: EventBroker,
    ) -> None:
        self.session_id = session_id
        self.project = project
        self.skill_root = skill_root
        self.node_service = node_service
        self.session_dir = session_dir
        self.store = store
        self.broker = broker
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._message_serial = 0
        self._assistant_message = "assistant-0"
        self._assistant_had_delta = False
        self._last_model_error: str | None = None

    async def start(self, config: dict[str, Any], *, resume: bool = False) -> None:
        node = node_executable()
        if not self.node_service.is_file():
            raise RuntimeError("LearnTrace UI 的 Pi Agent 服务未包含在当前安装包中。")
        self.session_dir.mkdir(parents=True, exist_ok=True)
        flags = 0x08000000 | 0x00000200 if os.name == "nt" else 0
        self._process = await asyncio.create_subprocess_exec(
            node,
            str(self.node_service),
            cwd=self.project,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=flags,
            env=agent_environment(),
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self._command(
            "start",
            cwd=str(self.project),
            skillPath=str(self.skill_root),
            sessionDir=str(self.session_dir),
            resume=resume,
            config=config,
        )
        self.store.update_session(
            self.session_id,
            state="ready",
        )

    async def _command(self, kind: str, **payload: Any) -> dict[str, Any]:
        request_id = secrets.token_urlsafe(10)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"id": request_id, "type": kind, "sessionId": self.session_id, **payload})
        try:
            response = await asyncio.wait_for(future, timeout=30)
        finally:
            self._pending.pop(request_id, None)
        if not response.get("success", False):
            raise RuntimeError(str(response.get("error") or f"Agent command failed: {kind}"))
        return response

    async def _send(self, payload: dict[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise RuntimeError("内置 Agent 服务未运行。")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        async with self._write_lock:
            self._process.stdin.write(encoded + b"\n")
            await self._process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._process and self._process.stdout
        try:
            while raw := await self._process.stdout.readline():
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "response":
                    future = self._pending.get(str(message.get("id")))
                    if future and not future.done():
                        future.set_result(cast(dict[str, Any], message))
                elif message.get("type") == "event":
                    event = message.get("event")
                    if isinstance(event, dict):
                        await self._handle_event(cast(dict[str, Any], event))
        finally:
            if not self._closing:
                await self._fail(RuntimeError("内置 Agent 服务意外退出。"))

    async def _read_stderr(self) -> None:
        assert self._process and self._process.stderr
        while raw := await self._process.stderr.readline():
            message = raw.decode("utf-8", errors="replace").strip()
            if message:
                await self.broker.publish(
                    self.session_id,
                    "compatibility_diagnostic",
                    {"runtime": "embedded-pi-sdk", "message": message},
                )

    async def _handle_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        if kind == "session_ready":
            await self.broker.publish(
                self.session_id,
                "session_config",
                {
                    "configOptions": [
                        {
                            "id": "thinking",
                            "name": "思考",
                            "type": "select",
                            "currentValue": event.get("thinking_level", "medium"),
                            "options": [
                                {"value": value, "name": value}
                                for value in cast(list[str], event.get("thinking_options") or [])
                            ],
                        }
                    ]
                },
            )
            await self.broker.publish(
                self.session_id,
                "session_state",
                {"state": "ready", "runtime": "embedded-pi-sdk"},
            )
            return
        if kind == "user_input_requested":
            request_id = str(event.get("request_id"))
            payload = {key: value for key, value in event.items() if key != "type"}
            if self.store.create_request(self.session_id, request_id, "user_input", payload):
                await self.broker.publish(self.session_id, kind, payload)
            return
        if kind == "message_start":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                self._message_serial += 1
                self._assistant_message = f"assistant-{self._message_serial}"
                self._assistant_had_delta = False
            return
        if kind == "message_update":
            update = event.get("assistantMessageEvent")
            if isinstance(update, dict) and update.get("type") == "text_delta":
                text = str(update.get("delta", ""))
                if text:
                    self._last_model_error = None
                    self._assistant_had_delta = True
                    await self.broker.publish(
                        self.session_id,
                        "message_delta",
                        {"role": "agent", "text": text, "message_id": self._assistant_message},
                    )
            return
        if kind == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("stopReason") == "error":
                self._last_model_error = str(message.get("errorMessage") or "模型请求失败。")
                return
            if isinstance(message, dict) and message.get("role") == "assistant":
                self._last_model_error = None
                if not self._assistant_had_delta:
                    text = _message_text(message)
                    if text:
                        await self.broker.publish(
                            self.session_id,
                            "message_completed",
                            {"role": "agent", "text": text},
                        )
            return
        if kind.startswith("tool_execution_"):
            mapped = {
                "tool_execution_start": "tool_started",
                "tool_execution_update": "tool_updated",
                "tool_execution_end": "tool_completed",
            }[kind]
            await self.broker.publish(
                self.session_id,
                mapped,
                {
                    "update": {
                        "toolCallId": event.get("toolCallId"),
                        "title": _tool_title(event),
                        "status": "completed" if kind == "tool_execution_end" else "in_progress",
                        "isError": bool(event.get("isError")),
                    }
                },
            )
            return
        if kind == "agent_start":
            self.store.update_session(
                self.session_id, state="running", error_code=None, error_message=None
            )
            await self.broker.publish(self.session_id, "session_state", {"state": "running"})
            return
        if kind == "runtime_error":
            message = str(event.get("message") or "Agent 运行出错。")
            await self._fail(RuntimeError(message))
            return
        if kind == "agent_settled":
            if self._last_model_error:
                await self._fail(RuntimeError(self._last_model_error))
            else:
                self.store.update_session(self.session_id, state="ready")
                await self.broker.publish(
                    self.session_id, "session_completed", {"stop_reason": "settled"}
                )
            return
        if kind in {"auto_retry_start", "compaction_start"}:
            await self.broker.publish(
                self.session_id,
                "agent_update",
                {"runtime": "embedded-pi-sdk", "update": event},
            )

    async def prompt(self, text: str, *, visible: bool = True) -> None:
        if visible:
            await self.broker.publish(
                self.session_id, "message_completed", {"role": "user", "text": text}
            )
        # The Node service acknowledges a prompt immediately and runs the model
        # turn asynchronously (service.ts).  Waiting on its response tied the
        # whole turn to the 30s command timeout, which falsely failed long model
        # turns.  Fire the command and return; the Node side reports any failure
        # through a `runtime_error` event, handled in `_handle_event` -> `_fail`.
        try:
            await self._send(
                {
                    "id": secrets.token_urlsafe(10),
                    "type": "prompt",
                    "sessionId": self.session_id,
                    "text": text,
                }
            )
        except Exception as error:
            await self._fail(error)

    async def answer(self, request_id: str, value: Any) -> None:
        await self._command("answer", requestId=request_id, value=value)

    async def set_thinking(self, value: str) -> None:
        await self._command("configure", config={"thinkingLevel": value})

    async def cancel(self) -> None:
        await self._command("abort")
        self.store.update_session(self.session_id, state="cancelled")
        await self.broker.publish(self.session_id, "session_state", {"state": "cancelled"})

    async def _fail(self, error: Exception) -> None:
        code, message = agent_error_details(error)
        self.store.update_session(
            self.session_id, state="failed", error_code=code, error_message=message
        )
        await self.broker.publish(
            self.session_id, "session_failed", {"code": code, "message": message}
        )

    async def close(self) -> None:
        self._closing = True
        if self._process and self._process.returncode is None:
            with contextlib.suppress(RuntimeError, TimeoutError):
                await self._command("close")
        if self._process and self._process.stdin:
            self._process.stdin.close()
        if self._process:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except TimeoutError:
                self._process.terminate()
                await self._process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._stderr_task) if task),
            return_exceptions=True,
        )


__all__ = [
    "EmbeddedAgentRuntime",
    "agent_environment",
    "agent_error_details",
    "node_executable",
    "probe_model",
]
