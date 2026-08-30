"""Focused tests for the Codex CLI JSONL session adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from learntrace.adapters import (
    TraceInputStatus,
    UnsupportedTraceFormatError,
    adapt_codex_export,
    adapt_codex_exports,
)
from learntrace.models import ContractValidator, EventKind, SourceType

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "codex" / "authorized-session.jsonl"


def _session_meta(session_id: str = "ses_main") -> dict[str, Any]:
    return {
        "timestamp": "2026-08-20T10:00:00.000Z",
        "type": "session_meta",
        "payload": {"id": session_id, "cwd": "/private/project"},
    }


def _function_call(
    *,
    call_id: str = "call_main",
    name: str = "shell",
    arguments: str = '{"command": ["bash", "-lc", "git status"]}',
    timestamp: str | None = "2026-08-20T10:00:01.000Z",
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": name,
            "arguments": arguments,
            "call_id": call_id,
        },
    }
    if timestamp is not None:
        record["timestamp"] = timestamp
    return record


def _local_shell_call(
    *,
    call_id: str = "call_local",
    command: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "timestamp": "2026-08-20T10:00:02.000Z",
        "type": "response_item",
        "payload": {
            "type": "local_shell_call",
            "call_id": call_id,
            "action": {"type": "exec", "command": command or ["git", "status"]},
        },
    }


def _call_output(
    call_id: str = "call_main",
    *,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "timestamp": "2026-08-20T10:00:03.000Z",
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(
                {"output": "TOOL_OUTPUT_MUST_NOT_LEAK", "metadata": {"exit_code": exit_code}}
            ),
        },
    }


def _raw_call_output(call_id: str = "call_main", *, output: str) -> dict[str, Any]:
    return {
        "timestamp": "2026-08-20T10:00:03.000Z",
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": call_id, "output": output},
    }


def _write_session(path: Path, *records: dict[str, Any]) -> Path:
    text = "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records)
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

    result = adapt_codex_export(export_path, authorized=False)

    assert result.status is TraceInputStatus.NOT_AUTHORIZED
    assert result.events == ()
    assert result.warnings == ()


def test_no_trace_statuses_are_explicit_and_have_no_events(tmp_path: Path) -> None:
    not_provided = adapt_codex_export(None, authorized=True)
    missing = adapt_codex_export(tmp_path / "missing.jsonl", authorized=True)
    no_calls = adapt_codex_export(
        _write_session(tmp_path / "no-calls.jsonl", _session_meta()),
        authorized=True,
    )

    assert not_provided.status is TraceInputStatus.NOT_PROVIDED
    assert missing.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert no_calls.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert not_provided.events == missing.events == no_calls.events == ()


def test_paired_function_call_becomes_a_stable_valid_trace_event(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _call_output(),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    event = result.events[0]
    expected_ref = "trace://codex/ses_main/response_item/call_main"
    assert event.source_refs[0].ref == expected_ref
    assert event.source_refs[0].type is SourceType.TRACE_RECORD
    assert event.source_refs[0].note == "codex"
    # 第二个引用指向会话导出文件本身（证据回读“去哪看原文”的落点）
    assert len(event.source_refs) == 2
    assert event.source_refs[1].type is SourceType.FILE
    assert event.source_refs[1].ref == session_path.resolve().as_posix()
    assert event.source_refs[1].note == "session-export"
    assert event.kind is EventKind.TRACE_RECORD
    assert event.id == f"evt-trace-{hashlib.sha256(expected_ref.encode()).hexdigest()[:16]}"
    assert event.summary == "Codex 工具 shell 已完成。 命令类型：bash。"
    assert event.occurred_at == "2026-08-20T10:00:01Z"
    validator = ContractValidator()
    validator.validate("observable_event", event.to_dict())


def test_local_shell_call_summarizes_the_command(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _local_shell_call(),
        _call_output("call_local"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 local_shell 已完成。 命令类型：git status。"


def test_nonzero_exit_code_marks_the_event_as_error(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(arguments='{"command": ["uv", "run", "pytest"]}'),
        _call_output(exit_code=1),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 shell 以错误结束。 命令类型：uv run pytest。"


def test_oversized_arguments_are_never_parsed(tmp_path: Path) -> None:
    oversized = json.dumps({"patch": "x" * (70 * 1024)})
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(name="apply_patch", arguments=oversized),
        _call_output(),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 apply_patch 已完成。"


def test_exec_command_cmd_key_is_summarized(tmp_path: Path) -> None:
    """Codex 0.149's exec_command tool passes the command as a ``cmd`` string."""
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(name="exec_command", arguments='{"cmd": "cat src/calc.py"}'),
        _call_output(),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 exec_command 已完成。 命令类型：cat。"


def test_unparseable_arguments_fall_back_to_a_generic_summary(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(arguments="not-json"),
        _call_output(),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 shell 已完成。"


def test_session_id_falls_back_to_file_stem(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "rollout-abc123.jsonl",
        _function_call(),
        _call_output(),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].source_refs[0].ref.startswith("trace://codex/rollout-abc123/")


def test_unmatched_call_and_orphan_output_are_skipped_with_warnings(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(call_id="call_never"),
        _call_output("call_ghost"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.events == ()
    assert result.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    codes = sorted(warning.code for warning in result.warnings)
    assert codes == ["incomplete_tool_call", "orphan_tool_result"]


def test_invalid_line_is_skipped_with_a_safe_warning(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(
        '{"type": "response_item", "payload": {"type": "function_call"\n',
        encoding="utf-8",
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert [warning.code for warning in result.warnings] == ["invalid_line"]


def test_oversized_session_is_rejected_without_path_leak(tmp_path: Path) -> None:
    session_path = tmp_path / "large.jsonl"
    with session_path.open("wb") as handle:
        handle.write(b"{}\n")
        handle.truncate(257 * 1024 * 1024)

    with pytest.raises(UnsupportedTraceFormatError) as exc_info:
        adapt_codex_export(session_path, authorized=True)

    assert "256 MiB" in str(exc_info.value)
    assert str(tmp_path) not in str(exc_info.value)


def test_duplicate_call_is_skipped(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(call_id="call_dup"),
        _function_call(call_id="call_dup"),
        _call_output("call_dup"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert any(warning.code == "duplicate_tool_call" for warning in result.warnings)


def test_event_cap_stops_constructing_events_at_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beyond the cap no further events are built, so parse memory stays bounded."""

    records: list[dict[str, Any]] = [_session_meta()]
    for index in range(3):
        records.append(
            _function_call(
                call_id=f"call_{index}",
                timestamp=f"2026-08-20T10:00:0{index + 1}.000Z",
            )
        )
        records.append(_call_output(f"call_{index}"))
    session_path = _write_session(tmp_path / "session.jsonl", *records)

    monkeypatch.setattr("learntrace.adapters.codex._MAX_EVENTS_PER_SESSION", 2)

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 2
    assert result.events[0].occurred_at == "2026-08-20T10:00:01Z"
    assert result.events[1].occurred_at == "2026-08-20T10:00:02Z"
    cap_warning = next(
        warning for warning in result.warnings if warning.code == "event_cap_reached"
    )
    assert "其余 1 条工具记录未导入" in cap_warning.message


def test_text_exit_code_in_plain_output_is_recognized_as_error(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _raw_call_output(output="Exit code: 1"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 shell 以错误结束。 命令类型：bash。"


def test_process_exited_with_code_text_is_recognized_as_error(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _raw_call_output(output="Process exited with code 1"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert "以错误结束" in result.events[0].summary


def test_zero_text_exit_code_is_reported_as_completed(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _raw_call_output(output="Exit code: 0"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert "已完成" in result.events[0].summary


def test_top_level_exit_code_json_is_recognized_as_error(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _raw_call_output(output='{"exit_code": 1}'),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert "以错误结束" in result.events[0].summary


def test_unconfirmable_exit_status_is_reported_as_unknown(tmp_path: Path) -> None:
    """A result without any observable exit signal must not claim success."""

    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _raw_call_output(output='{"output": "some tool output"}'),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 shell 结束（状态未知）。 命令类型：bash。"


def test_custom_tool_call_is_reported_as_unsupported(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        {
            "timestamp": "2026-08-20T10:00:02.000Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "call_custom",
                "name": "web_search",
                "input": "FORBIDDEN_TOOL_INPUT",
            },
        },
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.events == ()
    assert any(warning.code == "unsupported_call_type" for warning in result.warnings)
    serialized = json.dumps([warning.message for warning in result.warnings], ensure_ascii=False)
    assert "FORBIDDEN_TOOL_INPUT" not in serialized


def test_tracked_call_cap_bounds_pending_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records: list[dict[str, Any]] = [_session_meta()]
    for index in range(3):
        records.append(_function_call(call_id=f"call_{index}"))
    session_path = _write_session(tmp_path / "session.jsonl", *records)

    monkeypatch.setattr("learntrace.adapters.codex._MAX_TRACKED_CALLS", 2)

    result = adapt_codex_export(session_path, authorized=True)

    assert result.events == ()
    incomplete = [w for w in result.warnings if w.code == "incomplete_tool_call"]
    assert len(incomplete) == 2
    cap_warning = next(
        warning for warning in result.warnings if warning.code == "tracked_call_cap_reached"
    )
    assert "其余 1 条调用记录未导入" in cap_warning.message


def test_warning_cap_collapses_repeated_issues_into_one_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = ['{"type": "response_item", "payload": {"type": "function_call"'] * 10
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr("learntrace.adapters._jsonl._MAX_WARNINGS_PER_SESSION", 3)

    result = adapt_codex_export(session_path, authorized=True)

    detailed = [w for w in result.warnings if w.code == "invalid_line"]
    assert len(detailed) == 3
    summary = next(warning for warning in result.warnings if warning.code == "warning_cap_reached")
    assert "另有 7 条警告" in summary.message


def test_event_cap_summary_survives_detail_warning_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Truncation summaries survive even when detail warnings were dropped."""

    lines = ['{"type": "response_item", "payload": {"type": "function_call"'] * 5
    records = [
        _session_meta(),
        _function_call(call_id="call_a"),
        _call_output("call_a"),
        _function_call(call_id="call_b"),
        _call_output("call_b"),
    ]
    lines.extend(json.dumps(record, ensure_ascii=False) for record in records)
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr("learntrace.adapters._jsonl._MAX_WARNINGS_PER_SESSION", 3)
    monkeypatch.setattr("learntrace.adapters.codex._MAX_EVENTS_PER_SESSION", 1)

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    codes = {warning.code for warning in result.warnings}
    assert "warning_cap_reached" in codes
    assert "event_cap_reached" in codes


def test_tracked_call_cap_summary_survives_detail_warning_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pending-call cap summary survives a full detail warning quota too."""

    lines = ['{"type": "response_item", "payload": {"type": "function_call"'] * 5
    records = [
        _session_meta(),
        _function_call(call_id="call_a"),
        _function_call(call_id="call_b"),
    ]
    lines.extend(json.dumps(record, ensure_ascii=False) for record in records)
    session_path = tmp_path / "session.jsonl"
    session_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr("learntrace.adapters._jsonl._MAX_WARNINGS_PER_SESSION", 3)
    monkeypatch.setattr("learntrace.adapters.codex._MAX_TRACKED_CALLS", 1)

    result = adapt_codex_export(session_path, authorized=True)

    assert result.events == ()
    codes = {warning.code for warning in result.warnings}
    assert "warning_cap_reached" in codes
    assert "tracked_call_cap_reached" in codes


def test_arguments_over_the_byte_cap_are_never_parsed(tmp_path: Path) -> None:
    """The argument size guard counts UTF-8 bytes, not characters."""

    arguments = json.dumps({"command": "echo " + "啊" * 22000}, ensure_ascii=False)
    assert len(arguments) < 64 * 1024
    assert len(arguments.encode("utf-8")) > 64 * 1024
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(call_id="call_wide", arguments=arguments),
        _call_output("call_wide"),
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert len(result.events) == 1
    event = result.events[0]
    assert event.summary == "Codex 工具 shell 已完成。"
    assert "啊" not in json.dumps(event.to_dict(), ensure_ascii=False)


def test_lone_surrogate_arguments_do_not_abort_parsing(tmp_path: Path) -> None:
    """A lone surrogate in the byte-cap middle range must not abort the parse."""

    arguments = '{"command": "echo", "pad": "' + "x" * 20000 + "\ud800" + '"}'
    assert 64 * 1024 // 4 < len(arguments) <= 64 * 1024
    records = (_session_meta(), _function_call(arguments=arguments), _call_output())
    session_path = tmp_path / "session.jsonl"
    # Default ensure_ascii keeps the lone surrogate as a \uXXXX escape on disk;
    # json.loads restores it as an unencodable str inside the adapter.
    session_path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8"
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert result.events[0].summary == "Codex 工具 shell 已完成。"


def test_lone_surrogate_output_is_reported_as_unknown(tmp_path: Path) -> None:
    """A lone surrogate in a call output leaves the exit status unknown."""

    output = "y" * 20000 + "\ud800"
    records = (_session_meta(), _function_call(), _raw_call_output(output=output))
    session_path = tmp_path / "session.jsonl"
    session_path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8"
    )

    result = adapt_codex_export(session_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert "结束（状态未知）" in result.events[0].summary


def test_fixture_session_is_safely_adapted_without_leaks() -> None:
    result = adapt_codex_export(FIXTURE_PATH, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 3
    summaries = [event.summary for event in result.events]
    assert summaries == [
        "Codex 工具 shell 已完成。 命令类型：bash。",
        "Codex 工具 apply_patch 以错误结束。",
        "Codex 工具 local_shell 已完成。 命令类型：git status。",
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
        "FORBIDDEN_INSTRUCTIONS",
        "FORBIDDEN_CHAT_TEXT",
        "FORBIDDEN_TOKEN",
        "FORBIDDEN_SHELL_OUTPUT",
        "FORBIDDEN_PATCH_CONTENT",
        "FORBIDDEN_PATCH_OUTPUT",
        "FORBIDDEN_ERROR_DETAIL",
        "FORBIDDEN_LOCAL_OUTPUT",
        "FORBIDDEN_UNMATCHED_COMMAND",
        "FORBIDDEN_ORPHAN_OUTPUT",
        "Users",
        "Fixture Person",
        "TOKEN=",
        "--short",
    ):
        assert forbidden not in serialized


def test_exports_require_per_path_authorization(tmp_path: Path) -> None:
    authorized_session = _write_session(
        tmp_path / "authorized.jsonl",
        _session_meta(),
        _function_call(),
        _call_output(),
    )
    unlisted_missing = tmp_path / "unlisted-and-missing.jsonl"

    result = adapt_codex_exports(
        (authorized_session, unlisted_missing),
        authorized_paths=(authorized_session,),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["export_not_authorized"]


def test_exports_without_any_authorization_are_not_read(tmp_path: Path) -> None:
    session_path = _write_session(
        tmp_path / "session.jsonl",
        _session_meta(),
        _function_call(),
        _call_output(),
    )

    result = adapt_codex_exports((session_path,), authorized_paths=())

    assert result.status is TraceInputStatus.NOT_AUTHORIZED
    assert result.events == ()


def test_exports_merge_and_dedup_overlapping_sessions(tmp_path: Path) -> None:
    first = _write_session(
        tmp_path / "first.jsonl",
        _session_meta(),
        _function_call(),
        _call_output(),
    )
    second = _write_session(
        tmp_path / "second.jsonl",
        _session_meta(session_id="ses_second"),
        _function_call(call_id="call_other"),
        _call_output("call_other"),
    )
    overlap = _write_session(
        tmp_path / "overlap.jsonl",
        _session_meta(),
        _function_call(),
        _call_output(),
    )

    result = adapt_codex_exports(
        (first, second, overlap),
        authorized_paths=(first, second, overlap),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 2
    assert any(warning.code == "duplicate_export_event" for warning in result.warnings)
    notes = {event.source_refs[0].note for event in result.events}
    assert notes == {"codex"}
