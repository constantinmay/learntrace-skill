# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from learntrace.ui.app import create_app
from learntrace.ui.config import UIPaths, UISettings
from learntrace.ui.model_config import ModelConfig, ModelConfigStore


def _settings(tmp_path: Path) -> UISettings:
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    paths = UIPaths(
        data,
        cache,
        data / "ui.sqlite3",
        data / "pi-sessions",
    )
    return UISettings.create(tmp_path, port=8765, paths=paths)


LAUNCH_TOKEN = "test-launch-token"


def _app(settings):
    return create_app(settings, launch_token=LAUNCH_TOKEN)


def _client(application):
    client = TestClient(application, base_url="http://127.0.0.1")
    client.cookies.set("lt_token", LAUNCH_TOKEN)
    return client


def test_sessions_endpoint_rejects_missing_launch_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/sessions").status_code == 401


def test_sessions_endpoint_allows_valid_launch_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with _client(_app(settings)) as client:
        assert client.get("/api/v1/sessions").status_code == 200


def test_api_rejects_wrong_token_and_foreign_host(tmp_path: Path) -> None:
    application = _app(_settings(tmp_path))
    with TestClient(application, base_url="http://127.0.0.1") as client:
        client.cookies.set("lt_token", "wrong-token")
        assert client.get("/api/v1/sessions").status_code == 401
        assert (
            client.get("/api/v1/sessions", headers={"host": "attacker.example"}).status_code == 403
        )
        assert client.get("/", headers={"host": "attacker.example"}).status_code == 403


def test_bootstrap_sets_http_only_launch_cookie(tmp_path: Path) -> None:
    application = _app(_settings(tmp_path))
    with TestClient(application, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/bootstrap", headers={"Authorization": f"Bearer {LAUNCH_TOKEN}"}
        )
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert "lt_token=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


def test_event_stream_rejects_missing_launch_token(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(_app(settings), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/sessions/none/events?after=0").status_code == 401


def test_model_probe_uses_saved_key_and_pi_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def saved_key(_store: ModelConfigStore) -> str:
        return "stored-secret"

    monkeypatch.setattr("learntrace.ui.app.ModelConfigStore.api_key", saved_key)

    async def fake_probe(_service: Path, config: dict[str, object]) -> None:
        observed.update(config)

    monkeypatch.setattr("learntrace.ui.app.probe_model", fake_probe)
    with _client(_app(_settings(tmp_path))) as client:
        response = client.post(
            "/api/v1/model-config/probe",
            json={
                "base_url": "https://api.example.com",
                "model_id": "model-name",
                "api_protocol": "openai-completions",
                "thinking_level": "medium",
            },
        )

    assert response.status_code == 200
    assert observed["apiKey"] == "stored-secret"
    assert observed["modelId"] == "model-name"


def test_slow_credential_store_does_not_block_the_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_key(_store: ModelConfigStore) -> None:
        time.sleep(0.2)

    monkeypatch.setattr("learntrace.ui.app._CREDENTIAL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("learntrace.ui.app.ModelConfigStore.api_key", slow_key)

    with _client(_app(_settings(tmp_path))) as client:
        started = time.monotonic()
        response = client.get("/api/v1/model-config")
        elapsed = time.monotonic() - started
        sessions = client.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["has_api_key"] is False
    assert "凭据库响应超时" in response.json()["credential_error"]
    assert sessions.status_code == 200
    assert elapsed < 0.15


def test_model_config_requires_an_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_key(_store: ModelConfigStore) -> None:
        return None

    monkeypatch.setattr("learntrace.ui.app.ModelConfigStore.api_key", missing_key)

    with _client(_app(_settings(tmp_path))) as client:
        response = client.put(
            "/api/v1/model-config",
            json={
                "base_url": "https://api.example.com",
                "model_id": "model-name",
                "api_protocol": "openai-completions",
                "thinking_level": "medium",
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "请填写 API Key。"


def test_artifact_path_cannot_escape_project(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with _client(_app(settings)) as client:
        assert client.get("/api/v1/sessions/absent/artifacts/../../secret").status_code in {
            404,
            422,
        }


def test_session_api_does_not_expose_removed_acp_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session({"id": "other", "project": str(tmp_path / "other")})
    with _client(application) as client:
        item = client.get("/api/v1/sessions").json()[0]
        assert "agent_session_id" not in item
        assert "resumable" not in item
        assert "default_permissions" not in item


def test_deleting_session_removes_its_embedded_agent_transcript(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session(
        {
            "id": "deadbeef",
            "project": str(tmp_path),
            "title": "test",
        }
    )
    assert settings.paths is not None
    transcript_dir = settings.paths.pi_sessions / "deadbeef"
    transcript_dir.mkdir(parents=True)
    transcript = transcript_dir / "session.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")

    with _client(application) as client:
        response = client.delete("/api/v1/sessions/deadbeef")

    assert response.status_code == 200
    assert not transcript.exists()


def test_saved_session_replays_events_without_claiming_a_live_runtime(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session(
        {
            "id": "history",
            "project": str(tmp_path),
            "title": "saved analysis",
        }
    )
    manager.store.append_event("history", "analysis_started", {})
    manager.store.append_event(
        "history", "message_completed", {"role": "agent", "text": "saved reply"}
    )

    with _client(application) as client:
        snapshot = client.get("/api/v1/sessions/history/snapshot").json()

    detail = snapshot["session"]
    event_log = snapshot["events"]
    assert detail["runtime_connected"] is False
    assert detail["state"] == "archived"
    assert [event["type"] for event in event_log] == [
        "analysis_started",
        "message_completed",
    ]


def test_saved_session_includes_authorized_conversation_sources(tmp_path: Path) -> None:
    export = tmp_path / "session.jsonl"
    export.write_text("{}\n", encoding="utf-8")
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session({"id": "with-source", "project": str(tmp_path)})
    manager.store.add_conversation_source(
        "with-source",
        {
            "path": str(export.resolve()),
            "source_type": "codex",
            "authorization": "task3_parse",
            "state": "authorized",
            "size": export.stat().st_size,
        },
    )

    with _client(application) as client:
        snapshot = client.get("/api/v1/sessions/with-source/snapshot").json()

    assert snapshot["session"]["conversation_sources"] == [
        {
            "path": str(export.resolve()),
            "source_type": "codex",
            "authorization": "task3_parse",
            "state": "authorized",
            "size": export.stat().st_size,
            "created_at": snapshot["session"]["conversation_sources"][0]["created_at"],
        }
    ]


def test_configuration_failure_cannot_keep_using_old_runtime(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session({"id": "failed", "project": str(tmp_path)})
    manager.store.update_session(
        "failed",
        state="failed",
        error_code="agent_model_invalid",
        error_message="模型不存在。",
    )
    with _client(application) as client:
        manager.runtimes["failed"] = object()  # type: ignore[assignment]
        detail = manager.session_detail("failed")
        response = client.post("/api/v1/sessions/failed/messages", json={"text": "继续"})
        manager.runtimes.pop("failed")

    assert detail["runtime_connected"] is True
    assert detail["can_send"] is False
    assert detail["mode"] == "history"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "session_not_live"


def test_saved_pi_session_can_be_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    assert settings.paths is not None
    ModelConfigStore(settings.paths.model_config).save(
        ModelConfig("https://api.example.com", "model-name", "openai-completions"),
        None,
    )

    def saved_key(_store: ModelConfigStore) -> str:
        return "stored-secret"

    monkeypatch.setattr("learntrace.ui.app.ModelConfigStore.api_key", saved_key)
    observed: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *_args: object) -> None:
            pass

        async def start(self, _config: dict[str, object], *, resume: bool = False) -> None:
            observed["resume"] = resume

        async def close(self) -> None:
            pass

    monkeypatch.setattr("learntrace.ui.app.EmbeddedAgentRuntime", FakeRuntime)
    application = _app(settings)
    application.state.manager.store.create_session(
        {"id": "resumable", "project": str(tmp_path), "title": "saved"}
    )
    application.state.manager.store.append_event("resumable", "analysis_started", {})

    with _client(application) as client:
        response = client.post("/api/v1/sessions/resumable/resume")

    assert response.status_code == 200
    assert response.json()["can_send"] is True
    assert response.json()["mode"] == "live"
    assert observed["resume"] is True


def test_unstarted_session_reopens_without_fake_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    assert settings.paths is not None
    ModelConfigStore(settings.paths.model_config).save(
        ModelConfig("https://api.example.com", "model-name", "openai-completions"),
        None,
    )

    def saved_key(_store: ModelConfigStore) -> str:
        return "stored-secret"

    monkeypatch.setattr("learntrace.ui.app.ModelConfigStore.api_key", saved_key)
    observed: dict[str, object] = {}

    class FakeRuntime:
        def __init__(self, *_args: object) -> None:
            pass

        async def start(self, _config: dict[str, object], *, resume: bool = False) -> None:
            observed["resume"] = resume

        async def close(self) -> None:
            pass

    monkeypatch.setattr("learntrace.ui.app.EmbeddedAgentRuntime", FakeRuntime)
    application = _app(settings)
    application.state.manager.store.create_session(
        {"id": "not-started", "project": str(tmp_path), "title": "saved"}
    )

    with _client(application) as client:
        response = client.post("/api/v1/sessions/not-started/resume")

    assert response.status_code == 200
    assert observed["resume"] is False


def test_historical_artifact_uses_immutable_session_snapshot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session({"id": "history-artifact", "project": str(tmp_path)})
    report = tmp_path / "learning-record.md"
    report.write_text("old report\n", encoding="utf-8")
    old_bytes = report.read_bytes()
    asyncio.run(manager.refresh_artifacts("history-artifact"))
    stored = manager.store.list_artifacts("history-artifact")
    record = next(item for item in stored if item["kind"] == "intermediate_record")
    assert record["details"]["snapshot_path"].startswith(
        ".learntrace/ui-sessions/history-artifact/snapshots/"
    )
    report.write_text("later run\n", encoding="utf-8")

    with _client(application) as client:
        response = client.get("/api/v1/sessions/history-artifact/artifacts/learning-record.md")

    assert response.status_code == 200
    assert response.content == old_bytes


def test_historical_citation_uses_snapshotted_audit_archive(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    application = _app(settings)
    manager = application.state.manager
    manager.store.create_session({"id": "history-evidence", "project": str(tmp_path)})
    archive = tmp_path / ".learntrace" / "archive-records.json"
    archive.parent.mkdir()
    archive.write_text(
        json.dumps({"events": [{"id": "evt-original", "summary": "original"}]}),
        encoding="utf-8",
    )
    asyncio.run(manager.refresh_artifacts("history-evidence"))
    archive.write_text(
        json.dumps({"events": [{"id": "evt-later", "summary": "later"}]}),
        encoding="utf-8",
    )

    with _client(application) as client:
        original = client.get("/api/v1/sessions/history-evidence/evidence/evt-original")
        later = client.get("/api/v1/sessions/history-evidence/evidence/evt-later")

    assert original.status_code == 200
    assert original.json()["record"]["summary"] == "original"
    assert "snapshots" in original.json()["source"]
    assert later.status_code == 404


class _RecordingRuntime:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def prompt(self, text: str, *, visible: bool = True) -> None:
        self.prompts.append(text)

    async def close(self) -> None:
        pass


def test_concurrent_start_fires_analysis_prompt_once(tmp_path: Path) -> None:
    application = _app(_settings(tmp_path))
    manager = application.state.manager
    session_id = "concurrent-start"
    manager.store.create_session({"id": session_id, "project": str(tmp_path), "title": "test"})
    runtime = _RecordingRuntime()
    manager.runtimes[session_id] = runtime  # type: ignore[assignment]

    async def scenario() -> list[object]:
        return await asyncio.gather(
            manager.start_analysis(session_id, "first request"),
            manager.start_analysis(session_id, "second request"),
            return_exceptions=True,
        )

    outcomes = asyncio.run(scenario())
    already_started = [item for item in outcomes if isinstance(item, ValueError)]
    assert len(already_started) == 1
    assert len(runtime.prompts) == 1
    assert manager.store.has_event(session_id, "analysis_started")


def test_same_project_second_session_start_rejected_while_running(
    tmp_path: Path,
) -> None:
    application = _app(_settings(tmp_path))
    manager = application.state.manager
    first = "first-session"
    second = "second-session"
    for session_id in (first, second):
        manager.store.create_session({"id": session_id, "project": str(tmp_path), "title": "test"})
    first_runtime = _RecordingRuntime()
    second_runtime = _RecordingRuntime()
    manager.runtimes[first] = first_runtime  # type: ignore[assignment]
    manager.runtimes[second] = second_runtime  # type: ignore[assignment]

    async def scenario() -> None:
        await manager.start_analysis(first, "start first")
        with pytest.raises(RuntimeError, match="同一项目已有分析正在运行"):
            await manager.start_analysis(second, "start second")

    asyncio.run(scenario())
    assert len(first_runtime.prompts) == 1
    assert len(second_runtime.prompts) == 0
