"""Focused tests for the Claude Code JSONL session adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from learntrace.adapters import (
    TraceInputStatus,
    UnsupportedTraceFormatError,
    adapt_claude_code_export,
    adapt_claude_code_exports,
)
from learntrace.models import ContractValidator, EventKind, SourceType

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "claude-code" / "authorized-session.jsonl"


def _tool_use_block(
    *,
    block_id: str = "toolu_main",
    name: str = "Read",
    input_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "tool_use",
        "id": block_id,
        "name": name,
        "input": input_data if input_data is not None else {"file_path": "src/app.py"},
    }


def _assistant_record(
    *blocks: dict[str, Any],
    message_id: str = "msg_main",
    session_id: str = "ses_main",
    timestamp: str | None = "2026-08-20T10:00:00Z",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "assistant",
        "sessionId": session_id,
        "uuid": message_id,
        "message": {"role": "assistant", "content": list(blocks)},
    }
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record


def _user_record(*blocks: dict[str, Any], session_id: str = "ses_main") -> dict[str, Any]:
    return {
        "type": "user",
        "sessionId": session_id,
        "uuid": f"msg_user_{len(blocks)}",
        "message": {"role": "user", "content": list(blocks)},
    }


def _tool_result_block(
    tool_use_id: str = "toolu_main",
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": "TOOL_OUTPUT_MUST_NOT_LEAK",
    }
    if is_error:
        result["is_error"] = True
    return result


def _write_session(path: Path, *records: dict[str, Any], trailing_newline: bool = True) -> Path:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    text = "\n".join(lines)
    if trailing_newline:
        text = f"{text}\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_authorization_is_checked_before_path_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_path = tmp_path / "must-not-be-touched.jsonl"

    def fail_if_touched(self: Path, *args: object, **kwargs: object) -> object:
        del self, args, kwargs
        raise AssertionError("authorization=false must not inspect the path")

    monkeypatch.setattr(Path, "stat", fail_if_touched)

    result = adapt_claude_code_export(export_path, authorized=False)

    assert result.status is TraceInputStatus.NOT_AUTHORIZED
    assert result.events == ()
    assert result.warnings == ()


def test_no_trace_statuses_are_explicit_and_have_no_events(tmp_path: Path) -> None:
    not_provided = adapt_claude_code_export(None, authorized=True)
    missing = adapt_claude_code_export(tmp_path / "missing.jsonl", authorized=True)
    no_tools = adapt_claude_code_export(
        _write_session(
            tmp_path / "no-tools.jsonl",
            _assistant_record({"type": "text", "text": "PRIVATE_CHAT"}),
        ),
        authorized=True,
    )

    assert not_provided.status is TraceInputStatus.NOT_PROVIDED
    assert missing.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert no_tools.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert not_provided.events == missing.events == no_tools.events == ()


def test_completed_tool_pair_becomes_a_stable_valid_trace_event(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
    )

    result = adapt_claude_code_export(
        session_path,
        authorized=True,
        project_root=Path("/repo"),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    event = result.events[0]
    expected_ref = "trace://claude-code/ses_main/message/msg_main/tool_use/toolu_main"
    assert event.source_refs[0].ref == expected_ref
    assert event.source_refs[0].type is SourceType.TRACE_RECORD
    assert event.source_refs[0].note == "claude-code"
    assert event.kind is EventKind.TRACE_RECORD
    assert event.id == f"evt-trace-{hashlib.sha256(expected_ref.encode()).hexdigest()[:16]}"
    assert event.summary == "Claude Code 工具 read 已完成。 路径：src/app.py。"
    assert event.occurred_at == "2026-08-20T10:00:00Z"
    validator = ContractValidator()
    validator.validate("observable_event", event.to_dict())


def test_error_result_marks_the_event_as_error(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _assistant_record(_tool_use_block(name="Bash", input_data={"command": "pytest -q"})),
        _user_record(_tool_result_block(is_error=True)),
    )

    result = adapt_claude_code_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Claude Code 工具 bash 以错误结束。 命令类型：pytest。"


def test_fixture_session_is_safely_adapted_without_leaks() -> None:
    result = adapt_claude_code_export(
        FIXTURE_PATH,
        authorized=True,
        project_root=Path("D:/repo"),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 3
    summaries = [event.summary for event in result.events]
    assert summaries == [
        "Claude Code 工具 read 已完成。 路径：src/app.py。",
        "Claude Code 工具 bash 已完成。 命令类型：git status。",
        "Claude Code 工具 write 以错误结束。 路径：src/new-module.ts。",
    ]
    codes = sorted(warning.code for warning in result.warnings)
    assert codes == ["incomplete_tool_call", "invalid_line", "orphan_tool_result"]
    serialized = json.dumps(
        [event.to_dict() for event in result.events]
        + [
            {"code": warning.code, "location": warning.location, "message": warning.message}
            for warning in result.warnings
        ],
        ensure_ascii=False,
    )
    for forbidden in (
        "FORBIDDEN_USER_CHAT",
        "FORBIDDEN_CHAT_TEXT",
        "FORBIDDEN_READ_OUTPUT",
        "FORBIDDEN_TOKEN",
        "FORBIDDEN_COMMAND_TAIL",
        "FORBIDDEN_BASH_OUTPUT",
        "FORBIDDEN_WRITE_CONTENT",
        "FORBIDDEN_ERROR_BODY",
        "FORBIDDEN_GREP_PATTERN",
        "FORBIDDEN_ORPHAN_OUTPUT",
        "Users",
        "Fixture Person",
        "TOKEN=",
        "--short",
    ):
        assert forbidden not in serialized


def test_invalid_lines_and_timestamps_are_skipped_with_safe_warnings(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(_assistant_record(_tool_use_block(), timestamp="not-a-date")),
                json.dumps(_user_record(_tool_result_block())),
                '{"type": "assistant", "tool_use": broken json',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = adapt_claude_code_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].occurred_at is None
    codes = sorted(warning.code for warning in result.warnings)
    assert codes == ["invalid_line", "invalid_timestamp"]


def test_invalid_utf8_line_is_skipped(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_bytes(b'{"type": "user", "tool_result": "\xff\xfe"}\n')

    result = adapt_claude_code_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert [warning.code for warning in result.warnings] == ["invalid_line"]


def test_truncated_final_line_without_newline_is_tolerated(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
        trailing_newline=False,
    )

    result = adapt_claude_code_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert any(warning.code == "truncated_final_line" for warning in result.warnings)


def test_oversized_session_is_rejected_without_path_leak(tmp_path: Path) -> None:
    session_path = tmp_path / "large.jsonl"
    with session_path.open("wb") as handle:
        handle.write(b"{}\n")
        handle.truncate(257 * 1024 * 1024)

    with pytest.raises(UnsupportedTraceFormatError) as exc_info:
        adapt_claude_code_export(session_path, authorized=True)

    assert "256 MiB" in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_duplicate_tool_call_is_skipped(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _assistant_record(_tool_use_block(), message_id="msg_main"),
        _assistant_record(_tool_use_block(), message_id="msg_main"),
        _user_record(_tool_result_block()),
    )

    result = adapt_claude_code_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert any(warning.code == "duplicate_tool_call" for warning in result.warnings)


def test_event_cap_keeps_earliest_events_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records: list[dict[str, Any]] = []
    for index in range(3):
        block_id = f"toolu_{index}"
        records.append(
            _assistant_record(
                _tool_use_block(block_id=block_id),
                message_id=f"msg_{index}",
                timestamp=f"2026-08-20T10:00:0{index}Z",
            )
        )
        records.append(_user_record(_tool_result_block(block_id)))
    session_path = _write_session(tmp_path / "session.jsonl", *records)

    monkeypatch.setattr("learntrace.adapters.claude_code._MAX_EVENTS_PER_SESSION", 2)

    result = adapt_claude_code_export(session_path, authorized=True)

    assert len(result.events) == 2
    assert result.events[0].occurred_at == "2026-08-20T10:00:00Z"
    assert result.events[1].occurred_at == "2026-08-20T10:00:01Z"
    assert any(warning.code == "event_cap_reached" for warning in result.warnings)


def test_exports_require_per_path_authorization(tmp_path: Path) -> None:
    authorized_session = _write_session(
        tmp_path / "authorized.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
    )
    unlisted_missing = tmp_path / "unlisted-and-missing.jsonl"

    result = adapt_claude_code_exports(
        (authorized_session, unlisted_missing),
        authorized_paths=(authorized_session,),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["export_not_authorized"]


def test_exports_without_any_authorization_are_not_read(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
    )

    result = adapt_claude_code_exports((session_path,), authorized_paths=())

    assert result.status is TraceInputStatus.NOT_AUTHORIZED
    assert result.events == ()


def test_exports_merge_and_dedup_overlapping_sessions(tmp_path: Path) -> None:
    first = _write_session(
        tmp_path / "first.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
    )
    second = _write_session(
        tmp_path / "second.jsonl",
        _assistant_record(
            _tool_use_block(block_id="toolu_other", name="Bash", input_data={"command": "git log"}),
            message_id="msg_other",
        ),
        _user_record(_tool_result_block("toolu_other")),
    )
    overlap = _write_session(
        tmp_path / "overlap.jsonl",
        _assistant_record(_tool_use_block()),
        _user_record(_tool_result_block()),
    )

    result = adapt_claude_code_exports(
        (first, second, overlap),
        authorized_paths=(first, second, overlap),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 2
    assert any(warning.code == "duplicate_export_event" for warning in result.warnings)
    notes = {event.source_refs[0].note for event in result.events}
    assert notes == {"claude-code"}


def test_large_session_streams_in_seconds(tmp_path: Path) -> None:
    filler = "x" * (1024 * 1024)
    lines: list[str] = []
    for index in range(40):
        lines.append(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "ses_main",
                    "uuid": f"msg_filler_{index}",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"missing_{index}",
                                "content": filler,
                            }
                        ],
                    },
                }
            )
        )
    lines.append(json.dumps(_assistant_record(_tool_use_block())))
    lines.append(json.dumps(_user_record(_tool_result_block())))
    session_path = tmp_path / "large.jsonl"
    session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    import time

    started = time.monotonic()
    result = adapt_claude_code_export(session_path, authorized=True)
    elapsed = time.monotonic() - started

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert elapsed < 20.0
