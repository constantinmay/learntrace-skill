# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from learntrace.ui.embedded_runtime import (
    EmbeddedAgentRuntime,
    agent_environment,
    agent_error_details,
    node_executable,
)
from learntrace.ui.events import EventBroker
from learntrace.ui.storage import UIStore


def test_missing_auth_has_an_actionable_message() -> None:
    code, message = agent_error_details(RuntimeError("API key is missing"))
    assert code == "agent_auth_required"
    assert "API 配置" in message


def test_cancel_expires_questions_and_preserves_paused_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = UIStore(tmp_path / "ui.sqlite3")
    store.create_session({"id": "cancel-check", "project": str(tmp_path)})
    store.create_request("cancel-check", "question", "user_input", {"question": "scope?"})
    runtime = EmbeddedAgentRuntime(
        "cancel-check",
        tmp_path,
        tmp_path,
        tmp_path / "service.mjs",
        tmp_path / "pi",
        store,
        EventBroker(store),
    )

    async def command(kind: str) -> None:
        assert kind == "abort"
        await runtime._handle_event(
            {"type": "runtime_error", "message": "This operation was aborted"}
        )
        await runtime._handle_event({"type": "agent_settled"})

    monkeypatch.setattr(runtime, "_command", command)
    try:
        asyncio.run(runtime.cancel())
        assert store.pending_requests("cancel-check") == []
        record = store.get_session("cancel-check")
        assert record is not None and record["state"] == "cancelled"
        assert not store.has_event("cancel-check", "session_failed")
    finally:
        store.close()


def test_timeout_is_reported_as_model_failure() -> None:
    code, message = agent_error_details(RuntimeError("Request timed out"))
    assert code == "agent_model_unavailable"
    assert "模型服务" in message


@pytest.mark.parametrize("detail", ["ENOTFOUND", "ECONNREFUSED", "fetch failed", "HTTP 503"])
def test_network_errors_are_retryable_model_failures(detail: str) -> None:
    assert agent_error_details(RuntimeError(detail))[0] == "agent_model_unavailable"


@pytest.mark.parametrize("detail", ["unknown model", "model not found", "HTTP 404"])
def test_model_lookup_errors_require_new_configuration(detail: str) -> None:
    assert agent_error_details(RuntimeError(detail))[0] == "agent_model_invalid"


@pytest.mark.parametrize("version", ["20.12.0", "22.19.0", "22.22.1"])
def test_runtime_requires_supported_node_version(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    def find_node(_command: str) -> str:
        return "node"

    def old_node(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["node", "--version"], 0, f"v{version}\n", "")

    monkeypatch.setattr("learntrace.ui.embedded_runtime.shutil.which", find_node)
    monkeypatch.setattr(
        "learntrace.ui.embedded_runtime.subprocess.run",
        old_node,
    )

    with pytest.raises(RuntimeError, match=r"22\.22\.2"):
        node_executable()


def test_runtime_accepts_supported_node_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def find_node(_command: str) -> str:
        return "node"

    def current_node(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["node", "--version"], 0, "v22.22.2\n", "")

    monkeypatch.setattr("learntrace.ui.embedded_runtime.shutil.which", find_node)
    monkeypatch.setattr(
        "learntrace.ui.embedded_runtime.subprocess.run",
        current_node,
    )

    assert node_executable() == "node"


def test_agent_environment_keeps_existing_path_and_exposes_current_commands() -> None:
    executable = Path(".venv") / "Scripts" / "python.exe"
    environment = agent_environment({"PATH": "E:/other/bin"}, executable=str(executable))
    assert environment["PATH"].split(os.pathsep)[0].replace("\\", "/").endswith(".venv/Scripts")
    assert "E:/other/bin" in environment["PATH"]


def test_packaged_pi_service_can_start_and_close_a_real_session(tmp_path: Path) -> None:
    if not shutil.which("node"):
        pytest.skip("Node.js is not installed")
    root = Path(__file__).resolve().parents[2]
    service = root / "src" / "learntrace" / "ui" / "agent-service.mjs"
    skill = root / "skills" / "learntrace"
    if not service.is_file():
        pytest.skip("packaged Agent service has not been built")
    store = UIStore(tmp_path / "ui.sqlite3")
    store.create_session({"id": "bundle-smoke", "project": str(tmp_path)})
    runtime = EmbeddedAgentRuntime(
        "bundle-smoke",
        tmp_path,
        skill,
        service,
        tmp_path / "pi-sessions",
        store,
        EventBroker(store),
    )

    async def exercise() -> None:
        await runtime.start(
            {
                "baseUrl": "https://api.example.com",
                "apiKey": "not-a-real-key",
                "modelId": "test-model",
                "api": "openai-completions",
                "thinkingLevel": "medium",
            }
        )
        await runtime.close()

    try:
        asyncio.run(exercise())
    finally:
        store.close()
