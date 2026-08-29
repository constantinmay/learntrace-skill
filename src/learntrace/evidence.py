"""按需证据回读导出：把报告引用的证据落到本地 .learntrace/evidence/。

方式一（payload 引用携带 evidence_location）不需要本模块；本模块实现方式二：
由宿主 agent 在需要时显式触发，逐文件导出并维护 index.json。边界约束：

1. 会话导出遵循授权等级——minimal 只导出授权适配后的 v0 事件（时间/宿主/
   工具类型/归一化路径/命令摘要五类字段），full 导出原文（随全文阅读授权）；
   evidence 文件不是绕过最小保留的通道。
2. 所有导出在写盘前经过 ``redact_sensitive_text``（大 limit 只做形态脱敏，
   不做截断）。
3. ``.learntrace/``（含 evidence/）在 .gitignore 指引内，不进入仓库。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from learntrace.adapters import (
    TraceInputStatus,
    adapt_claude_code_exports,
    adapt_codex_exports,
    adapt_opencode_exports,
)
from learntrace.models.records import ObservableEvent
from learntrace.parsers import read_git_commit_text
from learntrace.parsers._common import repo_root
from learntrace.privacy import redact_sensitive_text

# 只做形态脱敏、不触发长度截断（redact 默认 limit=160 只适用于摘要）。
_NO_TRUNCATION_LIMIT = 1_000_000_000
_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)
_WINDOWS_REPLACE_ERRORS = frozenset({5, 32})
_INDEX_NAME = "index.json"
_SESSION_ID_RE = re.compile(r"^trace://[^/]+/([^/]+)/")

_SESSION_EXPORT_ADAPTERS = {
    "opencode": adapt_opencode_exports,
    "claude-code": adapt_claude_code_exports,
    "codex": adapt_codex_exports,
}

# 最小保留导出的五类字段（时间/宿主/工具/相对路径/命令摘要）解析边界：
# 适配层 summary 固定形如「<宿主> 工具 <tool> <状态句>。 [命令类型：X。][ 路径：Y。]」，
# 宿主名与工具名均不含空格，按字面标记切分是确定性的。
_TOOL_MARK = " 工具 "
_COMMAND_MARK = " 命令类型："
_PATH_MARK = " 路径："
_STATUS_MARKS = ("已完成。", "以错误结束。", "在消息错误结束时未完成。", "结束（状态未知）。")

# 项目内文件导出的禁区：版本库元数据与系统产物（档案/复盘/待答问题）
# 本身就是派生结论，不能作为「原始证据」被回读，避免自我引用与越权外发。
_PROJECT_FILE_BLOCKED = (".git", ".learntrace")
_PROJECT_FILE_BLOCKED_NAMES = frozenset({"learning-record.md", "learning-questions.md"})


def _minimal_event_record(event: ObservableEvent, source: str) -> dict[str, str | None]:
    """把 v0 事件投影成最小保留的五个字段。

    只保留时间/宿主/工具/相对路径/命令摘要。宿主一律用授权来源名
    （opencode/codex/claude-code，不含主机用户名/路径信息）；路径与
    命令由适配层在构造 summary 前归一化（相对路径或不可识别占位符、
    仅可执行名），此处只做确定性切分，不引入新的解析面。
    """
    summary = str(event.summary)
    tool: str | None = None
    command: str | None = None
    path: str | None = None
    tool_span = summary.split(_TOOL_MARK, maxsplit=1)
    if len(tool_span) == 2:
        remainder = tool_span[1]
        command_index = remainder.find(_COMMAND_MARK)
        path_index = remainder.find(_PATH_MARK)
        cut = min((i for i in (command_index, path_index) if i != -1), default=len(remainder))
        # 工具名 = 状态句（如「已完成。」）之前的部分；工具名由适配层限定为
        # 安全字符集，不可能包含状态句，按标记切分是确定性的。
        head = remainder[:cut].rstrip()
        for status in _STATUS_MARKS:
            status_index = head.find(status)
            if status_index != -1:
                head = head[:status_index]
                break
        tool = head.rstrip()
        if command_index != -1:
            command = remainder[command_index + len(_COMMAND_MARK) :].removesuffix("。")
        if path_index != -1:
            path = remainder[path_index + len(_PATH_MARK) :].removesuffix("。")
    return {
        "time": event.occurred_at,
        "host": source,
        "tool": tool,
        "path": path,
        "command": command,
    }


@dataclass(frozen=True, slots=True)
class EvidenceIndexEntry:
    """index.json 中的一条导出记录（路径/来源/覆盖/截断/授权等级）。"""

    path: str
    kind: str
    source: str
    coverage: str
    truncated: bool
    authorization: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "source": self.source,
            "coverage": self.coverage,
            "truncated": self.truncated,
            "authorization": self.authorization,
        }


def evidence_dir(project_root: Path) -> Path:
    """Return the on-demand evidence export directory (never pre-generated)."""
    return repo_root(project_root) / ".learntrace" / "evidence"


def _redact_full(text: str) -> str:
    return redact_sensitive_text(text, limit=_NO_TRUNCATION_LIMIT)


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写出 UTF-8 文本，短暂重试 Windows 共享/访问冲突。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as error:
                winerror = getattr(error, "winerror", None)
                if winerror not in _WINDOWS_REPLACE_ERRORS or attempt == len(_REPLACE_RETRY_DELAYS):
                    raise
                time.sleep(_REPLACE_RETRY_DELAYS[attempt])
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _load_index(evidence: Path) -> list[dict[str, Any]]:
    index_path = evidence / _INDEX_NAME
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        cast("dict[str, Any]", entry)
        for entry in cast("list[object]", raw)
        if isinstance(entry, dict)
    ]


def _record_entry(evidence: Path, entry: EvidenceIndexEntry) -> None:
    """按导出路径去重后追加 index 条目并原子写出。"""
    entries = [item for item in _load_index(evidence) if item.get("path") != entry.path]
    entries.append(entry.to_dict())
    entries.sort(key=lambda item: str(item.get("path", "")))
    _atomic_write_text(
        evidence / _INDEX_NAME,
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
    )


def _safe_export_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._~-]", "_", value)[:128]
    return name or "unnamed"


def export_git_commit(project_root: Path, commit_id: str) -> EvidenceIndexEntry:
    """导出单个提交的完整文本（提交信息 + diff）到 evidence/git/。"""
    root = repo_root(project_root)
    text = _redact_full(read_git_commit_text(root, commit_id))
    short = commit_id.strip()[:12] or "commit"
    safe_short = _safe_export_name(short)
    relative = f"git/{safe_short}.diff.txt"
    evidence = evidence_dir(root)
    _atomic_write_text(evidence / relative, text)
    entry = EvidenceIndexEntry(
        path=relative,
        kind="git_commit",
        source=f"git commit {commit_id.strip()}",
        coverage="full commit (message + diff)",
        truncated=False,
        authorization="local",
    )
    _record_entry(evidence, entry)
    return entry


def export_session_evidence(
    project_root: Path,
    export_path: Path,
    *,
    source: str,
    authorization: str,
) -> EvidenceIndexEntry:
    """导出一个已授权会话的证据切片到 evidence/sessions/。

    minimal=只导出授权适配后的 v0 事件（五类字段）；full=导出原文（需全文
    阅读权限）。一次授权只覆盖一个导出文件，由调用方保证路径精确。
    """
    if source not in _SESSION_EXPORT_ADAPTERS:
        msg = f"unsupported session source: {source}"
        raise ValueError(msg)
    if authorization not in ("minimal", "full"):
        msg = f"unsupported authorization level: {authorization}"
        raise ValueError(msg)
    root = repo_root(project_root)
    if not export_path.is_file():
        msg = f"session export not found: {export_path}"
        raise FileNotFoundError(msg)
    evidence = evidence_dir(root)
    export_ref = export_path.resolve().as_posix()

    if authorization == "full":
        text = _redact_full(export_path.read_text(encoding="utf-8", errors="replace"))
        session_id = export_path.stem
        relative = f"sessions/{_safe_export_name(source)}-{_safe_export_name(session_id)}.full.txt"
        _atomic_write_text(evidence / relative, text)
        entry = EvidenceIndexEntry(
            path=relative,
            kind="session_export",
            source=f"session export {export_ref}",
            coverage="full session file (redacted)",
            truncated=False,
            authorization="full",
        )
        _record_entry(evidence, entry)
        return entry

    adapter = _SESSION_EXPORT_ADAPTERS[source]
    result = adapter(
        (export_path,),
        authorized_paths=(export_path,),
        project_root=root,
    )
    if result.status is not TraceInputStatus.PARSED:
        msg = (
            "session export produced no trace events "
            f"(status={result.status.value}); nothing to export"
        )
        raise ValueError(msg)
    lines = [
        json.dumps(_minimal_event_record(event, source), ensure_ascii=False)
        for event in result.events
    ]
    session_id = export_path.stem
    for event in result.events:
        match = _SESSION_ID_RE.match(event.source_refs[0].ref)
        if match:
            session_id = match.group(1)
            break
    relative = f"sessions/{_safe_export_name(source)}-{_safe_export_name(session_id)}.minimal.jsonl"
    _atomic_write_text(evidence / relative, "\n".join(lines) + "\n")
    entry = EvidenceIndexEntry(
        path=relative,
        kind="session_export",
        source=f"session export {export_ref}",
        coverage=f"minimal retention: {len(lines)} v0 events (time/host/tool/path/command only)",
        truncated=False,
        authorization="minimal",
    )
    _record_entry(evidence, entry)
    return entry


def export_project_file(project_root: Path, relative_path: str) -> EvidenceIndexEntry:
    """导出一个项目内文件（脱敏）到 evidence/files/，用于测试日志/文档回读。"""
    root = repo_root(project_root)
    parts = tuple(part.casefold() for part in Path(relative_path).parts)
    if any(part in _PROJECT_FILE_BLOCKED for part in parts) or (
        parts and parts[-1] in _PROJECT_FILE_BLOCKED_NAMES
    ):
        msg = f"file is reserved learntrace output and cannot be exported: {relative_path}"
        raise ValueError(msg)
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        msg = f"file is outside the project root: {relative_path}"
        raise ValueError(msg) from None
    if not candidate.is_file():
        msg = f"project file not found: {relative_path}"
        raise FileNotFoundError(msg)
    text = _redact_full(candidate.read_text(encoding="utf-8", errors="replace"))
    safe_relative = Path(*(_safe_export_name(part) for part in Path(relative_path).parts))
    relative = f"files/{safe_relative.as_posix()}"
    evidence = evidence_dir(root)
    _atomic_write_text(evidence / relative, text)
    entry = EvidenceIndexEntry(
        path=relative,
        kind="project_file",
        source=f"project file {relative_path}",
        coverage="full file (redacted)",
        truncated=False,
        authorization="local",
    )
    _record_entry(evidence, entry)
    return entry


def load_evidence_index(project_root: Path) -> tuple[dict[str, Any], ...]:
    """读取 evidence/index.json（不存在时返回空）。"""
    return tuple(_load_index(evidence_dir(project_root)))
