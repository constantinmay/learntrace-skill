"""Focused tests for the on-demand evidence read-back exports."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from learntrace.evidence import (
    evidence_dir,
    export_git_commit,
    export_project_file,
    export_session_evidence,
    is_full_read_authorized,
    load_evidence_index,
    record_full_read_authorization,
)

OPENCODE_FIXTURE = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=env,
    )


def _make_repository(root: Path) -> str:
    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Fixture User")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / "src").mkdir()
    (repository / "src" / "parser.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "src/parser.py")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-20T10:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-20T10:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "add parser module", env=commit_env)
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _codex_session(path: Path, session_id: str = "ses_codex") -> Path:
    records = [
        {
            "timestamp": "2026-08-20T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": "/private/project"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "arguments": json.dumps({"command": ["bash", "-lc", "git status"]}),
                "call_id": "call_one",
            },
            "timestamp": "2026-08-20T10:00:01.000Z",
        },
        {
            "timestamp": "2026-08-20T10:00:03.000Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_one",
                "output": json.dumps(
                    {"output": "TOOL_OUTPUT_MUST_NOT_LEAK", "metadata": {"exit_code": 0}}
                ),
            },
        },
    ]
    path.write_text(
        "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
        encoding="utf-8",
    )
    return path


def test_evidence_is_never_pre_generated(tmp_path: Path) -> None:
    assert load_evidence_index(tmp_path) == ()
    assert not evidence_dir(tmp_path).exists()


def test_git_commit_export_writes_redacted_text_and_index(tmp_path: Path) -> None:
    commit_id = _make_repository(tmp_path)
    repository = tmp_path / "repository"

    entry = export_git_commit(repository, commit_id)

    assert entry.path == f"git/{commit_id[:12]}.diff.txt"
    assert entry.kind == "git_commit"
    assert entry.source == f"git commit {commit_id}"
    assert entry.coverage == "full commit (message + diff)"
    assert entry.truncated is False
    assert entry.authorization == "local"
    text = (evidence_dir(repository) / entry.path).read_text(encoding="utf-8")
    assert "add parser module" in text
    assert "+VALUE = 1" in text
    assert commit_id in text
    (index,) = load_evidence_index(repository)
    assert index == entry.to_dict()


def test_opencode_minimal_session_export_keeps_only_the_five_field_events(
    tmp_path: Path,
) -> None:
    entry = export_session_evidence(
        tmp_path, OPENCODE_FIXTURE, source="opencode", authorization="minimal"
    )

    # 会话 id 从 trace:// 引用提取（ses_fixture），而不是文件名 stem
    assert entry.path == "sessions/opencode-ses_fixture.minimal.jsonl"
    assert entry.kind == "session_export"
    assert entry.authorization == "minimal"
    assert entry.coverage == ("minimal retention: 3 v0 events (time/host/tool/path/command only)")
    raw = (evidence_dir(tmp_path) / entry.path).read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.strip().splitlines()]
    assert len(lines) == 3
    # 每行只有五类字段（时间/宿主/工具/相对路径/命令摘要），无完整事件、无来源引用
    for event in lines:
        assert set(event) == {"time", "host", "tool", "path", "command"}
        assert event["host"] == "opencode"
    by_tool = {(event["tool"], event["path"], event["command"]) for event in lines}
    # read 事件：路径被归一化——项目外绝对路径落为不可识别占位符，无命令
    assert ("read", "[outside-project]", None) in by_tool
    # bash 事件：只有命令类型，无路径、无参数（TOKEN/命令尾巴全部不落盘）
    assert ("bash", None, "git status") in by_tool
    # 无参数工具（task）：工具名保留，路径/命令均为空
    assert ("task", None, None) in by_tool
    # 工具输出/聊天/任务正文一律不进入最小保留导出
    for marker in (
        "FORBIDDEN_CHAT_TEXT",
        "FORBIDDEN_READ_OUTPUT",
        "FORBIDDEN_BASH_OUTPUT",
        "FORBIDDEN_TASK_OUTPUT",
        "FORBIDDEN_TOKEN",
        "FORBIDDEN_COMMAND_TAIL",
        "FORBIDDEN_TASK_PROMPT",
        "workdir",
    ):
        assert marker not in raw
    # 绝对路径/用户名/home 不进入最小保留导出（fixture 里的 D:\repo、C:\Users、\repo\ 都不得出现）
    for marker in ("D:\\repo", "D:/repo", "C:\\Users", "C:/Users", "Fixture Person", "\\repo\\"):
        assert marker not in raw
    # 导出文件自身路径（含仓库绝对路径）不得出现在导出内容里
    assert str(OPENCODE_FIXTURE.resolve()).replace("\\", "/") not in raw
    assert OPENCODE_FIXTURE.resolve().as_posix() not in raw


def test_codex_minimal_session_export_names_the_file_by_session_id(tmp_path: Path) -> None:
    session = _codex_session(tmp_path / "codex-session.jsonl")

    entry = export_session_evidence(tmp_path, session, source="codex", authorization="minimal")

    assert entry.path == "sessions/codex-ses_codex.minimal.jsonl"
    raw = (evidence_dir(tmp_path) / entry.path).read_text(encoding="utf-8")
    assert entry.coverage == "minimal retention: 1 v0 events (time/host/tool/path/command only)"
    (event,) = (json.loads(line) for line in raw.strip().splitlines())
    assert event == {
        "time": "2026-08-20T10:00:01Z",
        "host": "codex",
        "tool": "shell",
        "path": None,
        # 命令摘要=可执行名（summarize_command 语义；bash 包装命令不穿透取子命令）
        "command": "bash",
    }
    assert "TOOL_OUTPUT_MUST_NOT_LEAK" not in raw


def test_full_session_export_is_redacted_but_not_truncated(tmp_path: Path) -> None:
    tail = "z" * 400
    session = tmp_path / "full-session.jsonl"
    session.write_text(f"api_key=SECRET123 sk-abcdefgh1234567890 {tail}\n", encoding="utf-8")

    # 全文导出前须先记录逐文件全文授权
    record_full_read_authorization(
        tmp_path, session, source="codex", authorized_at="2026-08-20T10:05:00+08:00"
    )
    entry = export_session_evidence(tmp_path, session, source="codex", authorization="full")

    assert entry.path == "sessions/codex-full-session.full.txt"
    assert entry.authorization == "full"
    assert entry.coverage == "full session file (redacted)"
    # index source 只含宿主名与文件名 stem，不落本机绝对路径
    assert entry.source == "session export codex/full-session"
    assert str(session.resolve()) not in entry.source
    text = (evidence_dir(tmp_path) / entry.path).read_text(encoding="utf-8")
    assert "api_key=[REDACTED]" in text
    assert "sk-abcdefgh1234567890" not in text
    assert "SECRET123" not in text
    # 大 limit：只做形态脱敏，不做 160 字符截断
    assert text.rstrip().endswith(tail)


def test_session_export_rejects_unknown_source_and_authorization(tmp_path: Path) -> None:
    session = _codex_session(tmp_path / "session.jsonl")

    with pytest.raises(ValueError, match="unsupported session source"):
        export_session_evidence(tmp_path, session, source="gitlab", authorization="minimal")
    with pytest.raises(ValueError, match="unsupported authorization level"):
        export_session_evidence(tmp_path, session, source="codex", authorization="partial")


def test_session_export_rejects_missing_file_and_eventless_export(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="session export not found"):
        export_session_evidence(
            tmp_path,
            tmp_path / "missing.json",
            source="opencode",
            authorization="minimal",
        )

    text_only = tmp_path / "text-only.json"
    text_only.write_text(
        json.dumps(
            {
                "info": {"id": "ses_empty", "version": "1.18.6"},
                "messages": [
                    {
                        "info": {
                            "id": "msg_1",
                            "sessionID": "ses_empty",
                            "role": "assistant",
                        },
                        "parts": [{"id": "prt_1", "type": "text", "text": "hi"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="produced no trace events"):
        export_session_evidence(tmp_path, text_only, source="opencode", authorization="minimal")


def test_project_file_export_redacts_and_records_index(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("api_key=SECRET123\nplain tail marker\n", encoding="utf-8")

    entry = export_project_file(tmp_path, "notes.md")

    assert entry.path == "files/notes.md"
    assert entry.kind == "project_file"
    assert entry.coverage == "full file (redacted)"
    assert entry.authorization == "local"
    text = (evidence_dir(tmp_path) / entry.path).read_text(encoding="utf-8")
    assert "api_key=[REDACTED]" in text
    assert "plain tail marker" in text
    assert "SECRET123" not in text


def test_project_file_export_rejects_paths_outside_the_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the project root"):
        export_project_file(tmp_path, "../outside.md")
    with pytest.raises(FileNotFoundError, match="project file not found"):
        export_project_file(tmp_path, "missing.md")


@pytest.mark.parametrize(
    "blocked",
    [
        "learning-record.md",
        ".learntrace/archive-records.json",
        ".learntrace/learning-questions.md",
        ".git/config",
        "docs/.git/packed-refs",
        "subdir/learning-questions.md",
    ],
)
def test_project_file_export_rejects_reserved_outputs(tmp_path: Path, blocked: str) -> None:
    """版本库元数据与系统产物不是原始证据，禁止经证据回读通道外发。"""
    candidate = tmp_path / blocked
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("sensitive\n", encoding="utf-8")

    with pytest.raises(ValueError, match="reserved learntrace output"):
        export_project_file(tmp_path, blocked)


def test_index_deduplicates_by_path_and_stays_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")

    export_project_file(tmp_path, "b.txt")
    export_project_file(tmp_path, "a.md")
    assert [item["path"] for item in load_evidence_index(tmp_path)] == [
        "files/a.md",
        "files/b.txt",
    ]

    # 重复导出同一路径：条目去重，不追加重复
    export_project_file(tmp_path, "a.md")
    assert [item["path"] for item in load_evidence_index(tmp_path)] == [
        "files/a.md",
        "files/b.txt",
    ]
    index_path = evidence_dir(tmp_path) / "index.json"
    assert index_path.read_text(encoding="utf-8").endswith("\n")


def test_minimal_index_json_has_no_absolute_session_path(tmp_path: Path) -> None:
    """评审阻塞: minimal evidence 的 index.json 不得泄露本机绝对会话路径。

    仅测试 minimal JSONL 内容不够——index.json（及 ``--list`` 输出）也必须是
    无用户名/home/绝对路径的来源标识。
    """
    # 会话导出文件放在一段含「用户名」的深层目录里，放大泄露风险
    home_like = tmp_path / "Users" / "Fixture Person" / "Sessions"
    home_like.mkdir(parents=True)
    session = home_like / "authorized-export.json"
    (session.write_bytes(OPENCODE_FIXTURE.read_bytes()))

    entry = export_session_evidence(tmp_path, session, source="opencode", authorization="minimal")

    # index.json 落盘内容
    index_path = evidence_dir(tmp_path) / "index.json"
    index_text = index_path.read_text(encoding="utf-8")
    (indexed,) = load_evidence_index(tmp_path)
    assert indexed["source"] == entry.source
    assert indexed["source"] == "session export opencode/ses_fixture"
    # 用户名 / home 片段 / 绝对路径 一律不得出现在 index.json 里
    for marker in (
        "Fixture Person",
        "Users",
        str(session.resolve()),
        session.resolve().as_posix(),
        str(home_like.resolve()),
    ):
        assert marker not in index_text
    # --list 走的是 load_evidence_index，输出与 index.json 同构，同样无路径
    listed = [item for item in load_evidence_index(tmp_path)]
    assert listed == json.loads(index_text)


def test_full_session_export_requires_per_file_full_read_authorization(
    tmp_path: Path,
) -> None:
    """评审阻塞: full 导出必须有逐文件全文授权记录, 仅改参数不能升级权限。"""
    session = _codex_session(tmp_path / "session-a.jsonl")

    # ① 无授权记录: 仅凭 --authorization full 不能读原文
    with pytest.raises(PermissionError, match="no per-file full-read"):
        export_session_evidence(tmp_path, session, source="codex", authorization="full")
    # ② 授权的是另一份文件: 不覆盖本文件（逐文件生效, 非全局开关）
    other = _codex_session(tmp_path / "session-b.jsonl")
    record_full_read_authorization(
        tmp_path, other, source="codex", authorized_at="2026-08-20T10:00:00+08:00"
    )
    with pytest.raises(PermissionError, match="no per-file full-read"):
        export_session_evidence(tmp_path, session, source="codex", authorization="full")
    # ③ 精确命中本文件的授权记录: 放行
    record_full_read_authorization(
        tmp_path, session, source="codex", authorized_at="2026-08-20T10:01:00+08:00"
    )
    entry = export_session_evidence(tmp_path, session, source="codex", authorization="full")
    assert entry.authorization == "full"
    assert entry.path == "sessions/codex-session-a.full.txt"


def test_full_read_authorization_is_per_file_and_reusable(tmp_path: Path) -> None:
    session_a = _codex_session(tmp_path / "a.jsonl")
    session_b = _codex_session(tmp_path / "b.jsonl")

    assert not is_full_read_authorized(tmp_path, session_a)
    record_full_read_authorization(
        tmp_path, session_a, source="codex", authorized_at="2026-08-20T10:00:00+08:00"
    )
    # 授权只覆盖记录指向的同一文件, 不波及别的文件
    assert is_full_read_authorized(tmp_path, session_a)
    assert not is_full_read_authorized(tmp_path, session_b)
    # 记录落在 .learntrace 下, 不进入 evidence 索引, 也不含文件内容
    record_file = tmp_path / ".learntrace" / "full-read-authorizations.json"
    assert record_file.exists()
    records = json.loads(record_file.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["source"] == "codex"
    assert records[0]["authorized_at"] == "2026-08-20T10:00:00+08:00"
    # 记录的是绝对路径（本机校验用）, 但 index.json 不受其影响
    assert records[0]["path"] == session_a.resolve().as_posix()
    # 重复记录同一文件不产生重复条目
    record_full_read_authorization(
        tmp_path, session_a, source="codex", authorized_at="2026-08-20T10:02:00+08:00"
    )
    records = json.loads(record_file.read_text(encoding="utf-8"))
    assert [item["path"] for item in records] == [session_a.resolve().as_posix()]


def test_record_full_read_authorization_rejects_missing_and_bad_source(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="session export not found"):
        record_full_read_authorization(
            tmp_path,
            tmp_path / "missing.jsonl",
            source="codex",
            authorized_at="2026-08-20T10:00:00+08:00",
        )
    session = _codex_session(tmp_path / "session.jsonl")
    with pytest.raises(ValueError, match="unsupported session source"):
        record_full_read_authorization(
            tmp_path,
            session,
            source="gitlab",
            authorized_at="2026-08-20T10:00:00+08:00",
        )
