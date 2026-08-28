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
    load_evidence_index,
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
    for event in lines:
        assert event["kind"] == "trace_record"
        assert "occurred_at" in event
        session_refs = [ref for ref in event["source_refs"] if ref["note"] == "session-export"]
        assert session_refs[0]["type"] == "file"
        assert session_refs[0]["ref"] == OPENCODE_FIXTURE.resolve().as_posix()
    # 工具输出/聊天/任务正文一律不进入最小保留导出
    for marker in (
        "FORBIDDEN_CHAT_TEXT",
        "FORBIDDEN_READ_OUTPUT",
        "FORBIDDEN_BASH_OUTPUT",
        "FORBIDDEN_TASK_OUTPUT",
        "FORBIDDEN_TOKEN",
        "FORBIDDEN_COMMAND_TAIL",
        "FORBIDDEN_TASK_PROMPT",
    ):
        assert marker not in raw


def test_codex_minimal_session_export_names_the_file_by_session_id(tmp_path: Path) -> None:
    session = _codex_session(tmp_path / "codex-session.jsonl")

    entry = export_session_evidence(tmp_path, session, source="codex", authorization="minimal")

    assert entry.path == "sessions/codex-ses_codex.minimal.jsonl"
    raw = (evidence_dir(tmp_path) / entry.path).read_text(encoding="utf-8")
    assert entry.coverage == "minimal retention: 1 v0 events (time/host/tool/path/command only)"
    (event,) = (json.loads(line) for line in raw.strip().splitlines())
    assert event["occurred_at"] == "2026-08-20T10:00:01Z"
    assert "Codex 工具 shell" in event["summary"]
    assert "TOOL_OUTPUT_MUST_NOT_LEAK" not in raw


def test_full_session_export_is_redacted_but_not_truncated(tmp_path: Path) -> None:
    tail = "z" * 400
    session = tmp_path / "full-session.jsonl"
    session.write_text(f"api_key=SECRET123 sk-abcdefgh1234567890 {tail}\n", encoding="utf-8")

    entry = export_session_evidence(tmp_path, session, source="codex", authorization="full")

    assert entry.path == "sessions/codex-full-session.full.txt"
    assert entry.authorization == "full"
    assert entry.coverage == "full session file (redacted)"
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
