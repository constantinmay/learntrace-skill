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


def test_runtime_requires_supported_node_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def find_node(_command: str) -> str:
        return "node"

    def old_node(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["node", "--version"], 0, "v20.12.0\n", "")

    monkeypatch.setattr("learntrace.ui.embedded_runtime.shutil.which", find_node)
    monkeypatch.setattr(
        "learntrace.ui.embedded_runtime.subprocess.run",
        old_node,
    )

    with pytest.raises(RuntimeError, match="22.19"):
        node_executable()


def test_runtime_accepts_supported_node_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def find_node(_command: str) -> str:
        return "node"

    def current_node(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["node", "--version"], 0, "v22.19.0\n", "")

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
