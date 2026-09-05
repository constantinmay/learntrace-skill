"""FastAPI application for the local LearnTrace product."""
# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import platform
import secrets
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from learntrace.ui.artifact_monitor import (
    artifact_fingerprint,
    inspect_artifacts,
    snapshot_artifact,
)
from learntrace.ui.config import UISettings
from learntrace.ui.conversation_sources import (
    deduplicate_sources,
    task3_prompt,
    validate_source,
)
from learntrace.ui.embedded_runtime import EmbeddedAgentRuntime, agent_error_details, probe_model
from learntrace.ui.events import EventBroker
from learntrace.ui.evidence_lookup import resolve_citation
from learntrace.ui.model_config import ModelConfig, ModelConfigStore
from learntrace.ui.storage import UIStore

_CREDENTIAL_TIMEOUT_SECONDS = 5.0
_CONFIGURATION_ERRORS = {
    "agent_auth_required",
    "agent_config_invalid",
    "agent_model_invalid",
    "agent_runtime_missing",
}


_UI_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ConversationSourceCreate(BaseModel):
    path: str = Field(min_length=1, max_length=32_000)
    source_type: str = Field(min_length=1, max_length=50)


def _empty_conversation_sources() -> list[ConversationSourceCreate]:
    return []


class SessionCreate(BaseModel):
    title: str = "LearnTrace 分析"
    project_dir: str | None = Field(default=None, min_length=1, max_length=32_000)
    conversation_sources: list[ConversationSourceCreate] = Field(
        default_factory=_empty_conversation_sources, max_length=100
    )


class ConversationPickerRequest(BaseModel):
    source_type: str = Field(min_length=1, max_length=50)


class ModelConfigUpdate(BaseModel):
    base_url: str = Field(min_length=1, max_length=2_000)
    api_key: str | None = Field(default=None, max_length=20_000)
    model_id: str = Field(min_length=1, max_length=500)
    api_protocol: str = "anthropic-messages"
    thinking_level: str = "medium"


class MessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


class ConfigChange(BaseModel):
    config_id: str = Field(min_length=1, max_length=500)
    value: str | bool


class RequestAnswer(BaseModel):
    request_id: str | None = None
    value: Any


class RuntimeManager:
    _PUBLIC_SESSION_FIELDS = (
        "id",
        "project",
        "title",
        "state",
        "created_at",
        "updated_at",
        "error_code",
        "error_message",
    )

    def __init__(self, settings: UISettings, store: UIStore, broker: EventBroker) -> None:
        self.settings = settings
        self.store = store
        self.broker = broker
        self.runtimes: dict[str, EmbeddedAgentRuntime] = {}
        self.artifact_tasks: dict[str, asyncio.Task[None]] = {}
        self.artifact_baselines: dict[str, dict[str, str]] = {}
        self._analysis_start_lock = asyncio.Lock()

    def _skill_path(self) -> Path:
        candidates = [
            Path(__file__).resolve().parents[3] / "skills" / "learntrace" / "SKILL.md",
            Path(__file__).resolve().parent / "skill" / "SKILL.md",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise FileNotFoundError("packaged LearnTrace Skill was not found")

    def node_service_path(self) -> Path:
        candidates = [
            Path(__file__).resolve().parents[3] / "agent" / "dist" / "service.js",
            Path(__file__).resolve().parent / "agent-service.mjs",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise FileNotFoundError("embedded LearnTrace Agent service was not found")

    def _new_runtime(self, session_id: str, project: Path) -> EmbeddedAgentRuntime:
        assert self.settings.paths is not None
        return EmbeddedAgentRuntime(
            session_id,
            project,
            self._skill_path().resolve().parent,
            self.node_service_path(),
            self.settings.paths.pi_sessions / session_id,
            self.store,
            self.broker,
        )

    async def create_session(
        self, body: SessionCreate, config: ModelConfig, api_key: str
    ) -> dict[str, Any]:
        project = (
            Path(body.project_dir).expanduser().resolve(strict=True)
            if body.project_dir
            else self.settings.project
        )
        if not project.is_dir():
            raise ValueError(f"项目路径不是目录：{project}")
        sources = deduplicate_sources(
            [validate_source(item.source_type, item.path) for item in body.conversation_sources]
        )
        session_id = uuid.uuid4().hex
        session_dir = project / ".learntrace" / "ui-sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        self.store.create_session(
            {
                "id": session_id,
                "project": str(project),
                "title": project.name if body.title == "LearnTrace 分析" else body.title,
            }
        )
        for source in sources:
            self.store.add_conversation_source(session_id, source)
        baseline = inspect_artifacts(project, session_id)
        self.artifact_baselines[session_id] = {
            item["path"]: item["fingerprint"] for item in baseline
        }
        for item in baseline:
            recorded = {
                **item,
                "status": "preexisting",
                "details": {**item.get("details", {}), "preexisting": True},
            }
            self.store.upsert_artifact(session_id, recorded)
        runtime = self._new_runtime(session_id, project)
        self.runtimes[session_id] = runtime
        try:
            await runtime.start(
                {
                    "baseUrl": config.base_url,
                    "apiKey": api_key,
                    "modelId": config.model_id,
                    "api": config.api_protocol,
                    "thinkingLevel": config.thinking_level,
                }
            )
            self.artifact_tasks[session_id] = asyncio.create_task(
                self._monitor_artifacts(session_id)
            )
        except Exception as error:
            code, message = agent_error_details(error)
            self.runtimes.pop(session_id, None)
            self.store.update_session(
                session_id, state="failed", error_code=code, error_message=message
            )
            raise RuntimeError(message) from error
        return self.session_detail(session_id)

    async def resume_session(
        self, session_id: str, config: ModelConfig, api_key: str
    ) -> dict[str, Any]:
        record = self.store.get_session(session_id)
        if not record:
            raise KeyError(session_id)
        current = self.runtimes.get(session_id)
        if current and record.get("error_code") not in _CONFIGURATION_ERRORS:
            return self.session_detail(session_id)
        if current:
            await current.close()
            self.runtimes.pop(session_id, None)
        project = Path(str(record["project"])).resolve(strict=True)
        if not project.is_dir():
            raise ValueError("原项目目录已不存在，无法恢复会话。")
        self.store.expire_pending_requests(session_id)
        has_agent_history = self.store.has_event(session_id, "analysis_started")
        runtime = self._new_runtime(session_id, project)
        self.runtimes[session_id] = runtime
        try:
            await runtime.start(
                {
                    "baseUrl": config.base_url,
                    "apiKey": api_key,
                    "modelId": config.model_id,
                    "api": config.api_protocol,
                    "thinkingLevel": config.thinking_level,
                },
                resume=has_agent_history,
            )
            self.store.update_session(
                session_id, state="ready", error_code=None, error_message=None
            )
            self.artifact_tasks[session_id] = asyncio.create_task(
                self._monitor_artifacts(session_id)
            )
            await self.broker.publish(
                session_id,
                "session_resumed",
                {"agent_history_restored": has_agent_history},
            )
        except Exception as error:
            self.runtimes.pop(session_id, None)
            code, message = agent_error_details(error)
            self.store.update_session(
                session_id, state="failed", error_code=code, error_message=message
            )
            raise RuntimeError(message) from error
        return self.session_detail(session_id)

    def project_for(self, session_id: str) -> Path:
        record = self.store.get_session(session_id)
        if not record:
            raise KeyError(session_id)
        return Path(str(record["project"])).resolve()

    def _analysis_prompt(self, session_id: str, request: str) -> str:
        project = self.project_for(session_id)
        session_dir = project / ".learntrace" / "ui-sessions" / session_id
        return (
            "/skill:learntrace\n"
            f"用户的本次请求是：{request}\n"
            "请在当前项目中按 Skill 的授权、调查和完成条件工作。\n"
            "需要用户选择、授权、澄清或反思时，调用 request_user_input 工具，"
            "每次只提出一个清晰问题并等待回答。"
            "不要读取或导入本次 UI 产品会话作为 Task 3 历史轨迹。\n"
            f"{task3_prompt(self.store.list_conversation_sources(session_id))}"
            "本次 UI 会话的叙事产物目录和文件位置是：\n"
            f"- {session_dir / 'narrative-payload.json'}\n"
            f"- {session_dir / 'working-report.md'}\n"
            f"- {session_dir / 'final-report.md'}\n"
            "除此之外不增加执行步骤；请按 Skill 本身的授权、调查和完成条件工作。"
        )

    async def start_analysis(self, session_id: str, request: str) -> None:
        async with self._analysis_start_lock:
            await self._start_analysis_locked(session_id, request)

    async def deliver_analysis_message(self, session_id: str, text: str) -> None:
        async with self._analysis_start_lock:
            if self.store.has_event(session_id, "analysis_started"):
                runtime = await self.ensure_runtime(session_id)
                asyncio.create_task(runtime.prompt(text))
                return
            await self._start_analysis_locked(session_id, text)

    async def _start_analysis_locked(self, session_id: str, request: str) -> None:
        if self.store.has_event(session_id, "analysis_started"):
            raise ValueError("LearnTrace analysis has already started for this session")
        runtime = await self.ensure_runtime(session_id)
        project = str(self.project_for(session_id))
        for other in self.store.list_sessions(project):
            if other["id"] != session_id and other.get("state") == "running":
                raise RuntimeError(
                    "同一项目已有分析正在运行；请等待其完成或先取消，避免产物互相覆盖。"
                )
        self.store.update_session(session_id, state="running", error_code=None, error_message=None)
        await self.broker.publish(session_id, "analysis_started", {})
        await self.broker.publish(
            session_id, "message_completed", {"role": "user", "text": request}
        )
        asyncio.create_task(
            runtime.prompt(self._analysis_prompt(session_id, request), visible=False)
        )

    def session_detail(self, session_id: str) -> dict[str, Any]:
        record = self.store.get_session(session_id)
        if not record:
            raise KeyError(session_id)
        result = self.session_summary(record)
        result["pending_requests"] = self.store.pending_requests(session_id)
        result["artifacts"] = self.store.list_artifacts(session_id)
        result["conversation_sources"] = self.store.list_conversation_sources(session_id)
        return result

    def session_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        """Project persisted state onto the Pi UI's public session contract.

        The SQLite schema keeps a few columns for migration compatibility with
        early prototypes. They are storage details and must not leak back into
        the product API or become frontend state.
        """
        session_id = str(record["id"])
        result = {field: record.get(field) for field in self._PUBLIC_SESSION_FIELDS}
        result["analysis_started"] = self.store.has_event(session_id, "analysis_started")
        # Persistence and liveness are deliberately separate. A saved session
        # can always be inspected, but only a runtime owned by this process can
        # receive new prompts or publish live events.
        runtime_connected = session_id in self.runtimes
        can_send = runtime_connected and record.get("error_code") not in _CONFIGURATION_ERRORS
        result["runtime_connected"] = runtime_connected
        result["mode"] = "live" if can_send else "history"
        result["can_send"] = can_send
        return result

    async def ensure_runtime(self, session_id: str) -> EmbeddedAgentRuntime:
        record = self.store.get_session(session_id)
        if not record:
            raise KeyError(session_id)
        if record.get("error_code") in _CONFIGURATION_ERRORS:
            raise RuntimeError("模型配置已失效；请调整设置后新建一次分析。")
        current = self.runtimes.get(session_id)
        if current:
            return current
        raise RuntimeError("服务重启后，请从历史记录选择“恢复并继续”。")

    async def refresh_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        current = inspect_artifacts(self.project_for(session_id), session_id)
        previous = {item["path"]: item for item in self.store.list_artifacts(session_id)}
        for item in current:
            if self.artifact_baselines.get(session_id, {}).get(item["path"]) == item["fingerprint"]:
                item = {
                    **item,
                    "status": "preexisting",
                    "details": {**item.get("details", {}), "preexisting": True},
                }
            elif item["kind"] not in {"narrative_payload", "working_report", "final_report"}:
                snapshotted = await asyncio.to_thread(
                    snapshot_artifact,
                    self.project_for(session_id),
                    session_id,
                    item,
                )
                if snapshotted is None:
                    # The producer was still writing this file.  Do not persist
                    # a fingerprint/content pair that never existed together.
                    continue
                item = snapshotted
            self.store.upsert_artifact(session_id, item)
            if previous.get(item["path"], {}).get("fingerprint") != item["fingerprint"]:
                await self.broker.publish(session_id, "artifact_updated", item)
        return self.store.list_artifacts(session_id)

    async def _monitor_artifacts(self, session_id: str) -> None:
        try:
            while session_id in self.runtimes:
                await self.refresh_artifacts(session_id)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        for task in self.artifact_tasks.values():
            task.cancel()
        await asyncio.gather(*self.artifact_tasks.values(), return_exceptions=True)
        await asyncio.gather(
            *(runtime.close() for runtime in self.runtimes.values()), return_exceptions=True
        )
        self.store.close()


def create_app(settings: UISettings, *, launch_token: str | None = None) -> FastAPI:
    if settings.paths is None:
        raise ValueError("UI settings paths are required")
    store = UIStore(settings.paths.database)
    broker = EventBroker(store)
    manager = RuntimeManager(settings, store, broker)
    config_store = ModelConfigStore(settings.paths.model_config)

    async def saved_api_key() -> str | None:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(config_store.api_key),
                timeout=_CREDENTIAL_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            raise RuntimeError("系统凭据库响应超时；请稍后重试，或重新保存 API Key。") from error

    async def public_model_config() -> dict[str, object]:
        try:
            api_key = await saved_api_key()
        except RuntimeError as error:
            return config_store.public(has_api_key=False, credential_error=str(error))
        return config_store.public(has_api_key=bool(api_key))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Any session left "running" from a previous process is an orphan: no
        # runtime exists for it now.  Mark it interrupted so the UI shows the
        # truth instead of forever hanging on "正在分析".
        for record in store.list_sessions():
            if record.get("state") == "running":
                store.update_session(
                    record["id"],
                    state="interrupted",
                    error_code="agent_interrupted",
                    error_message="服务重启，分析已中断。",
                )
            elif record.get("state") in {"created", "ready"}:
                state = (
                    "archived"
                    if store.has_event(record["id"], "analysis_started")
                    else "interrupted"
                )
                store.update_session(
                    record["id"],
                    state=state,
                    error_code=None,
                    error_message=None,
                )
        yield
        await manager.close()

    app = FastAPI(
        title="LearnTrace", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.manager = manager
    token = launch_token if launch_token is not None else secrets.token_urlsafe(32)

    @app.middleware("http")
    async def authorize_and_secure(request: Request, call_next: Any) -> Response:
        hostname = (request.url.hostname or "").strip("[]").lower()
        if hostname not in _UI_LOOPBACK_HOSTS:
            return JSONResponse(
                {"detail": {"code": "forbidden_host", "message": "仅允许通过本机回环地址访问。"}},
                status_code=403,
            )
        if request.url.path.startswith("/api/"):
            auth_header = request.headers.get("authorization", "")
            bearer = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            presented = bearer or request.cookies.get("lt_token", "")
            if not presented or not secrets.compare_digest(presented, token):
                return JSONResponse(
                    {
                        "detail": {
                            "code": "unauthorized",
                            "message": "未授权访问；请重新运行 learntrace ui。",
                        }
                    },
                    status_code=401,
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/v1/bootstrap")
    async def bootstrap() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.set_cookie("lt_token", token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/v1/model-config")
    async def model_config() -> dict[str, object]:
        return await public_model_config()

    @app.put("/api/v1/model-config")
    async def update_model_config(body: ModelConfigUpdate) -> dict[str, object]:
        config = ModelConfig(
            body.base_url.strip(),
            body.model_id.strip(),
            body.api_protocol,
            body.thinking_level,
        )
        new_api_key = body.api_key.strip() if body.api_key else None
        try:
            existing_api_key = None if new_api_key else await saved_api_key()
            if not new_api_key and not existing_api_key:
                raise ValueError("请填写 API Key。")
            await asyncio.wait_for(
                asyncio.to_thread(config_store.save, config, new_api_key),
                timeout=_CREDENTIAL_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            raise HTTPException(
                503,
                detail={
                    "code": "credential_store_timeout",
                    "message": "系统凭据库保存超时；配置尚未确认保存，请重试。",
                },
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                503,
                detail={"code": "credential_store_unavailable", "message": str(error)},
            ) from error
        return config_store.public(has_api_key=bool(new_api_key or existing_api_key))

    @app.post("/api/v1/model-config/probe")
    async def probe_model_config(body: ModelConfigUpdate) -> dict[str, str]:
        config = ModelConfig(
            body.base_url.strip(),
            body.model_id.strip(),
            body.api_protocol,
            body.thinking_level,
        )
        try:
            config.validate()
            api_key = body.api_key.strip() if body.api_key else await saved_api_key()
            if not api_key:
                raise ValueError("请输入 API Key，或先保存一个 API Key。")
            await probe_model(
                manager.node_service_path(),
                {
                    "baseUrl": config.base_url,
                    "apiKey": api_key,
                    "modelId": config.model_id,
                    "api": config.api_protocol,
                    "thinkingLevel": config.thinking_level,
                },
            )
        except (OSError, RuntimeError, ValueError) as error:
            code, message = agent_error_details(error)
            raise HTTPException(422, detail={"code": code, "message": message}) from error
        return {"status": "connected", "message": "模型连接验证成功。"}

    @app.post("/api/v1/projects/select")
    async def select_project() -> dict[str, str | bool]:
        def open_dialog() -> str:
            system = platform.system()
            if system == "Windows":
                # FolderBrowserDialog runs in a separate STA process. It is the
                # native picker, rather than a browser upload control that would
                # hide the real filesystem path from LearnTrace.
                script = (
                    "Add-Type -AssemblyName System.Windows.Forms;"
                    "Add-Type -AssemblyName System.Drawing;"
                    "$owner=New-Object System.Windows.Forms.Form;"
                    "$owner.Size=New-Object System.Drawing.Size(1,1);"
                    "$owner.StartPosition='CenterScreen';"
                    "$owner.ShowInTaskbar=$false;$owner.Opacity=0;$owner.TopMost=$true;"
                    "$owner.Show();$owner.Activate();"
                    "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
                    "$dialog.Description='选择要分析的项目';"
                    "$dialog.ShowNewFolderButton=$false;"
                    "try{if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
                    "{[Console]::Out.Write($dialog.SelectedPath)}}"
                    "finally{$owner.Close();$owner.Dispose()}"
                )
                completed = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                )
                if completed.returncode != 0:
                    detail = completed.stderr.strip() or "系统目录选择器启动失败。"
                    raise RuntimeError(detail)
                return completed.stdout.strip()
            if system == "Linux" and shutil.which("zenity"):
                completed = subprocess.run(
                    [
                        "zenity",
                        "--file-selection",
                        "--directory",
                        "--title=选择要分析的项目",
                        f"--filename={settings.project}{Path('/').as_posix()}",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=300,
                )
                if completed.returncode == 1:
                    return ""  # User cancelled the dialog.
                if completed.returncode != 0:
                    raise RuntimeError(completed.stderr.strip() or "系统目录选择器启动失败。")
                return completed.stdout.strip()
            try:
                import tkinter as tk
                from tkinter import filedialog
            except ImportError as error:
                raise RuntimeError("当前系统没有可用的目录选择器，请粘贴项目路径。") from error
            root = tk.Tk()
            root.withdraw()
            try:
                return str(
                    filedialog.askdirectory(
                        title="选择要分析的项目",
                        initialdir=str(settings.project),
                        mustexist=True,
                    )
                )
            finally:
                root.destroy()

        try:
            selected = await asyncio.to_thread(open_dialog)
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                501,
                detail={"code": "directory_picker_unavailable", "message": str(error)},
            ) from error
        if not selected:
            return {"selected": False, "path": ""}
        path = Path(selected).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise HTTPException(422, detail={"code": "project_not_directory"})
        return {"selected": True, "path": str(path)}

    @app.post("/api/v1/conversation-sources/select")
    async def select_conversation_sources(
        body: ConversationPickerRequest,
    ) -> dict[str, Any]:
        # Validate the requested host before opening a native picker. The
        # selected files are only returned; they are not read until a session
        # is created with explicit authorization.
        try:
            if body.source_type not in {"opencode", "claude-code", "codex"}:
                raise ValueError(f"不支持的对话来源：{body.source_type}")

            def open_dialog() -> list[str]:
                system = platform.system()
                if system == "Windows":
                    script = (
                        "Add-Type -AssemblyName System.Windows.Forms;"
                        "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
                        "$dialog.Title='选择 AI 会话导出';"
                        "$dialog.Filter='AI 会话导出 (*.json;*.jsonl)|*.json;*.jsonl|"
                        "所有文件 (*.*)|*.*';"
                        "$dialog.Multiselect=$true;"
                        "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
                        "{$dialog.FileNames | ForEach-Object {[Console]::Out.WriteLine($_)}}"
                    )
                    completed = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=300,
                    )
                    if completed.returncode != 0:
                        raise RuntimeError(completed.stderr.strip() or "系统文件选择器启动失败。")
                    return [line for line in completed.stdout.splitlines() if line]
                if system == "Linux" and shutil.which("zenity"):
                    completed = subprocess.run(
                        [
                            "zenity",
                            "--file-selection",
                            "--multiple",
                            "--separator=\n",
                            "--title=选择 AI 会话导出",
                            "--file-filter=AI 会话导出 | *.json *.jsonl",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=300,
                    )
                    if completed.returncode == 1:
                        return []
                    if completed.returncode != 0:
                        raise RuntimeError(completed.stderr.strip() or "系统文件选择器启动失败。")
                    return [line for line in completed.stdout.splitlines() if line]
                try:
                    import tkinter as tk
                    from tkinter import filedialog
                except ImportError as error:
                    raise RuntimeError(
                        "当前系统没有可用的文件选择器，请粘贴导出文件路径。"
                    ) from error
                root = tk.Tk()
                root.withdraw()
                try:
                    return list(
                        filedialog.askopenfilenames(
                            title="选择 AI 会话导出",
                            filetypes=(("AI 会话导出", "*.json *.jsonl"), ("所有文件", "*.*")),
                        )
                    )
                finally:
                    root.destroy()

            selected = await asyncio.to_thread(open_dialog)
            sources = deduplicate_sources(
                [validate_source(body.source_type, path) for path in selected]
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                422,
                detail={"code": "conversation_source_invalid", "message": str(error)},
            ) from error
        return {"selected": bool(sources), "sources": sources}

    @app.get("/api/v1/sessions")
    async def sessions() -> list[dict[str, Any]]:
        records = store.list_sessions()
        return [manager.session_summary(record) for record in records]

    @app.get("/api/v1/sessions/{session_id}/snapshot")
    async def session_snapshot(session_id: str) -> dict[str, Any]:
        """Return one coherent, non-streaming view of a saved session.

        Historical sessions are UI records, not dormant Agent processes.  A
        snapshot therefore contains everything needed to render them without
        opening SSE or attempting runtime recovery.
        """
        try:
            detail = manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        return {
            "session": detail,
            "events": store.events_after(session_id, 0),
            "artifacts": detail["artifacts"],
            "pending_requests": detail["pending_requests"],
        }

    @app.post("/api/v1/sessions")
    async def create_session(body: SessionCreate) -> dict[str, Any]:
        try:
            config = config_store.load()
            api_key = await saved_api_key()
            if not config or not api_key:
                raise ValueError("请先保存 API 地址、模型 ID 和 API Key。")
            return await manager.create_session(body, config, api_key)
        except (OSError, ValueError, RuntimeError) as error:
            code, message = agent_error_details(error)
            raise HTTPException(422, detail={"code": code, "message": message}) from error

    @app.post("/api/v1/sessions/{session_id}/resume")
    async def resume_session(session_id: str) -> dict[str, Any]:
        try:
            config = config_store.load()
            api_key = await saved_api_key()
            if not config or not api_key:
                raise ValueError("请先保存并验证模型配置。")
            return await manager.resume_session(session_id, config, api_key)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        except (OSError, RuntimeError, ValueError) as error:
            code, message = agent_error_details(error)
            raise HTTPException(422, detail={"code": code, "message": message}) from error

    @app.get("/api/v1/sessions/{session_id}")
    async def session(session_id: str) -> dict[str, Any]:
        try:
            return manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error

    @app.get("/api/v1/sessions/{session_id}/events")
    async def events(request: Request, session_id: str, after: int = 0) -> StreamingResponse:
        try:
            manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        last = request.headers.get("last-event-id")
        cursor = int(last) if last and last.isdecimal() else after
        return StreamingResponse(
            broker.subscribe(session_id, cursor), media_type="text/event-stream"
        )

    @app.post("/api/v1/sessions/{session_id}/messages")
    async def message(session_id: str, body: MessageCreate) -> dict[str, bool]:
        try:
            await manager.ensure_runtime(session_id)
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                409, detail={"code": "session_not_live", "message": str(error)}
            ) from error
        await manager.deliver_analysis_message(session_id, body.text)
        return {"accepted": True}

    @app.post("/api/v1/sessions/{session_id}/start")
    async def start_analysis(session_id: str) -> dict[str, bool]:
        try:
            await manager.start_analysis(session_id, "请分析这个项目的学习与开发过程。")
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                409, detail={"code": "analysis_start_failed", "message": str(error)}
            ) from error
        return {"started": True}

    @app.post("/api/v1/sessions/{session_id}/config")
    async def configure(session_id: str, body: ConfigChange) -> dict[str, bool]:
        runtime = manager.runtimes.get(session_id)
        if not runtime:
            raise HTTPException(409, detail={"code": "session_not_live"})
        try:
            if body.config_id != "thinking":
                raise ValueError("API 或模型变更需要新建分析，避免混用上下文。")
            await runtime.set_thinking(str(body.value))
        except Exception as error:
            raise HTTPException(
                422, detail={"code": "config_change_failed", "message": str(error)}
            ) from error
        return {"accepted": True}

    @app.post(
        "/api/v1/sessions/{session_id}/answers/{request_id}",
    )
    async def answer(session_id: str, request_id: str, body: RequestAnswer) -> dict[str, bool]:
        runtime = manager.runtimes.get(session_id)
        if not runtime:
            raise HTTPException(409, detail={"code": "session_not_live"})
        if not store.answer_request(session_id, request_id, body.value):
            raise HTTPException(409, detail={"code": "request_already_answered"})
        await runtime.answer(request_id, body.value)
        await broker.publish(session_id, "request_answered", {"request_id": request_id})
        return {"accepted": True}

    @app.post("/api/v1/sessions/{session_id}/cancel")
    async def cancel(session_id: str) -> dict[str, bool]:
        runtime = manager.runtimes.get(session_id)
        if runtime:
            await runtime.cancel()
        return {"cancelled": True}

    @app.get("/api/v1/sessions/{session_id}/artifacts")
    async def artifacts(session_id: str) -> list[dict[str, Any]]:
        try:
            detail = manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        if detail["mode"] != "live":
            return store.list_artifacts(session_id)
        return await manager.refresh_artifacts(session_id)

    @app.get(
        "/api/v1/sessions/{session_id}/artifacts/{artifact_path:path}",
    )
    async def artifact_content(session_id: str, artifact_path: str) -> FileResponse:
        try:
            manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        detail = manager.session_detail(session_id)
        items = (
            await manager.refresh_artifacts(session_id)
            if detail["mode"] == "live"
            else store.list_artifacts(session_id)
        )
        allowed = {item["path"]: item for item in items}
        if artifact_path not in allowed:
            raise HTTPException(404, detail={"code": "artifact_not_found"})
        project = manager.project_for(session_id)
        stored = allowed[artifact_path]
        snapshot_path = stored.get("details", {}).get("snapshot_path")
        path = (project / (snapshot_path or artifact_path)).resolve()
        if project not in path.parents:
            raise HTTPException(403, detail={"code": "path_outside_project"})
        if not path.is_file():
            raise HTTPException(404, detail={"code": "artifact_not_found"})
        if artifact_fingerprint(path) != stored["fingerprint"]:
            raise HTTPException(
                409,
                detail={
                    "code": "artifact_changed",
                    "message": "该项目级产物已被后续分析更新；历史会话不会显示不属于它的内容。",
                },
            )
        return FileResponse(path)

    @app.get(
        "/api/v1/sessions/{session_id}/evidence/{citation_id}",
    )
    async def evidence(session_id: str, citation_id: str) -> dict[str, Any]:
        try:
            manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        try:
            artifacts = store.list_artifacts(session_id)
            audit = next((item for item in artifacts if item["kind"] == "audit_archive"), None)
            snapshot = audit.get("details", {}).get("snapshot_path") if audit else None
            return resolve_citation(
                manager.project_for(session_id),
                session_id,
                citation_id,
                archive_path=str(snapshot) if snapshot else None,
            )
        except KeyError as error:
            raise HTTPException(404, detail={"code": "citation_not_found"}) from error

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str) -> dict[str, bool]:
        try:
            manager.session_detail(session_id)
        except KeyError as error:
            raise HTTPException(404, detail={"code": "session_not_found"}) from error
        runtime = manager.runtimes.pop(session_id, None)
        task = manager.artifact_tasks.pop(session_id, None)
        if task:
            task.cancel()
        if runtime:
            await runtime.close()
        project = manager.project_for(session_id)
        session_dir = project / ".learntrace" / "ui-sessions" / session_id
        if session_dir.is_symlink():
            session_dir.unlink()
        elif session_dir.is_dir():
            shutil.rmtree(session_dir)
        assert settings.paths is not None
        pi_session_dir = settings.paths.pi_sessions / session_id
        if pi_session_dir.is_dir():
            shutil.rmtree(pi_session_dir)
        manager.artifact_baselines.pop(session_id, None)
        return {"deleted": store.delete_session(session_id)}

    static = Path(__file__).resolve().parent / "static"

    @app.get("/{path:path}")
    async def frontend(path: str) -> Response:
        candidate = (static / path).resolve()
        if path and static in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        index = static / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>LearnTrace UI</h1><p>Frontend assets have not been built.</p>", status_code=503
        )

    @app.exception_handler(ValueError)
    async def value_error(_: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(
            {"detail": {"code": "invalid_request", "message": str(error)}}, status_code=422
        )

    return app
