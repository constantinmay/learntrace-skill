"""Focused tests for the M1 OpenCode JSON adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from learntrace.adapters import (
    TraceInputStatus,
    UnsupportedOpenCodeFormatError,
    adapt_opencode_export,
    adapt_opencode_exports,
    write_trace_result,
)
from learntrace.models import ContractValidator, EventKind, SourceType


def _tool_part(
    *,
    part_id: str = "prt_read",
    message_id: str = "msg_main",
    session_id: str = "ses_main",
    tool: str = "read",
    status: str = "completed",
    input_data: dict[str, Any] | None = None,
    start: object = 1_700_000_000_000,
) -> dict[str, Any]:
    state: dict[str, Any] = {
        "status": status,
        "input": input_data if input_data is not None else {"filePath": "src/app.py"},
        "output": "TOOL_OUTPUT_MUST_NOT_LEAK",
    }
    if start is not None:
        state["time"] = {"start": start}
    return {
        "id": part_id,
        "sessionID": session_id,
        "messageID": message_id,
        "type": "tool",
        "tool": tool,
        "state": state,
    }


def _message(
    *parts: dict[str, Any],
    message_id: str = "msg_main",
    session_id: str = "ses_main",
    role: str = "assistant",
    error: object | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "id": message_id,
        "sessionID": session_id,
        "role": role,
        "time": {"created": 1},
    }
    if error is not None:
        info["error"] = error
    return {"info": info, "parts": list(parts)}


def _export(*messages: dict[str, Any], version: str = "1.18.6") -> dict[str, Any]:
    return {
        "info": {"id": "ses_main", "version": version},
        "messages": list(messages),
    }


def _write_export(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_authorization_is_checked_before_path_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_path = tmp_path / "must-not-be-touched.json"

    def fail_if_touched(self: Path, *args: object, **kwargs: object) -> object:
        del self, args, kwargs
        raise AssertionError("authorization=false must not inspect the path")

    monkeypatch.setattr(Path, "stat", fail_if_touched)

    result = adapt_opencode_export(export_path, authorized=False)

    assert result.status is TraceInputStatus.NOT_AUTHORIZED
    assert result.events == ()
    assert result.warnings == ()


def test_no_trace_statuses_are_explicit_and_have_no_events(tmp_path: Path) -> None:
    not_provided = adapt_opencode_export(None, authorized=True)
    missing = adapt_opencode_export(tmp_path / "missing.json", authorized=True)
    no_tools_path = _write_export(
        tmp_path / "no-tools.json",
        _export(_message({"id": "prt_text", "type": "text", "text": "PRIVATE_CHAT"})),
    )
    no_tools = adapt_opencode_export(no_tools_path, authorized=True)

    assert not_provided.status is TraceInputStatus.NOT_PROVIDED
    assert missing.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert no_tools.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert not_provided.events == missing.events == no_tools.events == ()


def test_completed_file_tool_becomes_a_stable_valid_trace_event(tmp_path: Path) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(_tool_part(start=0))),
    )

    result = adapt_opencode_export(
        export_path,
        authorized=True,
        project_root=tmp_path,
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    event = result.events[0]
    source_ref = "trace://opencode/ses_main/message/msg_main/part/prt_read"
    expected_id = f"evt-trace-{hashlib.sha256(source_ref.encode()).hexdigest()[:16]}"
    assert event.id == expected_id
    assert event.kind is EventKind.TRACE_RECORD
    assert event.occurred_at == "1970-01-01T00:00:00Z"
    assert "read" in event.summary
    assert "src/app.py" in event.summary
    assert event.source_refs[0].type is SourceType.TRACE_RECORD
    assert event.source_refs[0].ref == source_ref
    assert event.source_refs[0].note == "opencode"
    # 第二个引用指向导出文件本身（证据回读“去哪看原文”的落点）
    assert len(event.source_refs) == 2
    assert event.source_refs[1].type is SourceType.FILE
    assert event.source_refs[1].ref == export_path.resolve().as_posix()
    assert event.source_refs[1].note == "session-export"
    ContractValidator().validate("observable_event", event.to_dict())


def test_malformed_sibling_is_skipped_with_a_safe_warning(tmp_path: Path) -> None:
    malformed = _tool_part(part_id="prt_malformed")
    malformed["state"] = {
        "status": {"unexpected": True},
        "input": {"password": "RAW_SECRET"},
    }
    valid = _tool_part(part_id="prt_valid")
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(malformed, valid)),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert [event.source_refs[0].ref for event in result.events] == [
        "trace://opencode/ses_main/message/msg_main/part/prt_valid"
    ]
    assert [warning.code for warning in result.warnings] == ["invalid_tool_part"]
    assert "RAW_SECRET" not in json.dumps([warning.message for warning in result.warnings])


@pytest.mark.parametrize("session_id", ["s" * 129, "ses_\ud800"])
def test_invalid_batch_session_id_is_rejected_safely(
    tmp_path: Path,
    session_id: str,
) -> None:
    data = _export()
    data["info"]["id"] = session_id
    export_path = _write_export(tmp_path / "export.json", data)

    with pytest.raises(UnsupportedOpenCodeFormatError) as caught:
        adapt_opencode_export(export_path, authorized=True)

    assert session_id not in str(caught.value)


@pytest.mark.parametrize("message_id", ["m" * 129, "msg_\ud800"])
def test_invalid_message_id_is_skipped_without_losing_healthy_sibling(
    tmp_path: Path,
    message_id: str,
) -> None:
    invalid_message = _message(
        _tool_part(part_id="prt_invalid_message", message_id=message_id),
        message_id=message_id,
    )
    valid_message = _message(_tool_part(part_id="prt_valid"))
    export_path = _write_export(
        tmp_path / "export.json",
        _export(invalid_message, valid_message),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert [event.source_refs[0].ref for event in result.events] == [
        "trace://opencode/ses_main/message/msg_main/part/prt_valid"
    ]
    assert [warning.code for warning in result.warnings] == ["invalid_message"]
    assert message_id not in result.warnings[0].message


@pytest.mark.parametrize("part_id", ["p" * 129, "prt_\ud800"])
def test_invalid_part_id_is_skipped_without_losing_healthy_sibling(
    tmp_path: Path,
    part_id: str,
) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(
            _message(
                _tool_part(part_id=part_id),
                _tool_part(part_id="prt_valid"),
            )
        ),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert [event.source_refs[0].ref for event in result.events] == [
        "trace://opencode/ses_main/message/msg_main/part/prt_valid"
    ]
    assert [warning.code for warning in result.warnings] == ["invalid_tool_part"]
    assert part_id not in result.warnings[0].message


def test_only_matching_assistant_messages_can_produce_events(tmp_path: Path) -> None:
    valid = _message(_tool_part(part_id="prt_valid"))
    user_message = _message(
        _tool_part(part_id="prt_user", message_id="msg_user"),
        message_id="msg_user",
        role="user",
    )
    wrong_session = _message(
        _tool_part(
            part_id="prt_wrong_session",
            message_id="msg_wrong_session",
            session_id="ses_other",
        ),
        message_id="msg_wrong_session",
        session_id="ses_other",
    )
    export_path = _write_export(
        tmp_path / "export.json",
        _export(user_message, wrong_session, valid),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert len(result.events) == 1
    assert result.events[0].source_refs[0].ref.endswith("/part/prt_valid")
    assert [warning.code for warning in result.warnings] == ["invalid_message"]


def test_live_incomplete_parts_are_skipped_but_persisted_error_is_observable(
    tmp_path: Path,
) -> None:
    live_message = _message(
        _tool_part(
            part_id="prt_live",
            message_id="msg_live",
            status="pending",
            input_data={"prompt": "LIVE_PROMPT_SECRET"},
        ),
        message_id="msg_live",
    )
    errored_message = _message(
        _tool_part(
            part_id="prt_incomplete",
            message_id="msg_error",
            status="running",
            tool="task",
            input_data={
                "description": "PRIVATE_DESCRIPTION",
                "subagent_type": "PRIVATE_AGENT",
                "prompt": "PRIVATE_PROMPT",
            },
        ),
        message_id="msg_error",
        error={"message": "PRIVATE_ERROR_BODY"},
    )
    export_path = _write_export(
        tmp_path / "export.json",
        _export(live_message, errored_message),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert "未完成" in result.events[0].summary
    serialized = json.dumps(result.events[0].to_dict(), ensure_ascii=False)
    for forbidden in (
        "LIVE_PROMPT_SECRET",
        "PRIVATE_DESCRIPTION",
        "PRIVATE_AGENT",
        "PRIVATE_PROMPT",
        "PRIVATE_ERROR_BODY",
    ):
        assert forbidden not in serialized
    assert [warning.code for warning in result.warnings] == ["incomplete_tool_part"]


def test_summaries_use_strict_whitelists_and_never_copy_sensitive_fields(
    tmp_path: Path,
) -> None:
    bash = _tool_part(
        part_id="prt_bash",
        tool="bash",
        input_data={
            "command": "TOKEN=SECRETVALUE git status --short && echo PRIVATE_COMMAND_TAIL",
            "workdir": "C:\\Users\\Alice\\private-project",
        },
    )
    task = _tool_part(
        part_id="prt_task",
        tool="task",
        input_data={
            "description": "PRIVATE_DESCRIPTION",
            "subagent_type": "PRIVATE_AGENT",
            "prompt": "PRIVATE_PROMPT",
        },
    )
    external_file = _tool_part(
        part_id="prt_file",
        tool="write",
        input_data={
            "filePath": "C:\\Users\\Alice\\secrets.txt",
            "content": "PRIVATE_FILE_CONTENT",
        },
    )
    export_path = _write_export(
        tmp_path / "export.json",
        _export(
            _message(
                {"id": "prt_text", "type": "text", "text": "PRIVATE_CHAT"},
                bash,
                task,
                external_file,
            )
        ),
    )

    result = adapt_opencode_export(
        export_path,
        authorized=True,
        project_root=Path("C:/repo"),
    )

    serialized = json.dumps([event.to_dict() for event in result.events], ensure_ascii=False)
    assert "git status" in serialized
    assert "PRIVATE_COMMAND_TAIL" not in serialized
    assert "[outside-project]" in serialized
    for forbidden in (
        "SECRETVALUE",
        "PRIVATE_DESCRIPTION",
        "PRIVATE_AGENT",
        "PRIVATE_PROMPT",
        "PRIVATE_FILE_CONTENT",
        "PRIVATE_CHAT",
        "TOOL_OUTPUT_MUST_NOT_LEAK",
        "Users",
        "Alice",
    ):
        assert forbidden not in serialized


def test_unknown_tool_does_not_read_unapproved_path_fields(tmp_path: Path) -> None:
    marker = "FORBIDDEN_UNKNOWN_TOOL_INPUT"
    export_path = _write_export(
        tmp_path / "export.json",
        _export(
            _message(
                _tool_part(
                    tool="unknown_extension",
                    input_data={"path": marker},
                )
            )
        ),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert len(result.events) == 1
    assert "unknown_extension" in result.events[0].summary
    assert marker not in result.events[0].summary


def test_surrogate_in_file_path_is_redacted_before_result_write(tmp_path: Path) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(_tool_part(input_data={"filePath": "src/\ud800-private.txt"}))),
    )

    result = adapt_opencode_export(export_path, authorized=True)
    output_path = tmp_path / "trace-result.json"
    write_trace_result(result, output_path)

    assert "[unsafe-path]" in result.events[0].summary
    serialized = output_path.read_text(encoding="utf-8")
    assert "\ud800" not in serialized


@pytest.mark.parametrize(
    "raw",
    [
        "{not-json",
        "[]",
        '{"info": {"id": "ses_main", "version": "1.18.6"}}',
        '{"info": {"id": 1, "version": "1.18.6"}, "messages": []}',
    ],
)
def test_invalid_json_or_incompatible_root_is_rejected_safely(tmp_path: Path, raw: str) -> None:
    export_path = tmp_path / "private-name.json"
    export_path.write_text(raw, encoding="utf-8")

    with pytest.raises(UnsupportedOpenCodeFormatError) as caught:
        adapt_opencode_export(export_path, authorized=True)

    assert str(export_path) not in str(caught.value)


def test_deeply_nested_json_is_rejected_as_an_unsupported_export(tmp_path: Path) -> None:
    export_path = tmp_path / "deep.json"
    nested_messages = "[" * 1_500 + "0" + "]" * 1_500
    export_path.write_text(
        '{"info":{"id":"ses_main","version":"1.18.6"},"messages":' + nested_messages + "}",
        encoding="utf-8",
    )

    with pytest.raises(UnsupportedOpenCodeFormatError):
        adapt_opencode_export(export_path, authorized=True)


def test_oversized_json_integer_is_rejected_as_an_unsupported_export(tmp_path: Path) -> None:
    export_path = tmp_path / "oversized-integer.json"
    export_path.write_text(
        '{"info":{"id":"ses_main","version":"1.18.6"},"messages":[],"extra":' + "9" * 5_000 + "}",
        encoding="utf-8",
    )

    with pytest.raises(UnsupportedOpenCodeFormatError):
        adapt_opencode_export(export_path, authorized=True)


def test_oversized_export_is_rejected_without_exposing_its_path(tmp_path: Path) -> None:
    export_path = tmp_path / "private-large-export.json"
    with export_path.open("wb") as handle:
        handle.seek(32 * 1024 * 1024)
        handle.write(b"x")

    with pytest.raises(UnsupportedOpenCodeFormatError) as caught:
        adapt_opencode_export(export_path, authorized=True)

    assert "32 MiB" in str(caught.value)
    assert str(export_path) not in str(caught.value)


def test_structurally_compatible_unverified_version_parses_with_warning(
    tmp_path: Path,
) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(_tool_part()), version="2.0.0"),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["unverified_opencode_version"]


def test_invalid_timestamp_is_omitted_and_events_are_sorted_deterministically(
    tmp_path: Path,
) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(
            _message(
                _tool_part(part_id="prt_no_time", start=None),
                _tool_part(part_id="prt_late", start=2_000),
                _tool_part(part_id="prt_invalid", start=-1),
                _tool_part(part_id="prt_early", start=1_000),
            )
        ),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert [event.source_refs[0].ref.rsplit("/", maxsplit=1)[-1] for event in result.events] == [
        "prt_early",
        "prt_late",
        "prt_invalid",
        "prt_no_time",
    ]
    assert result.events[2].occurred_at is None
    assert [warning.code for warning in result.warnings] == ["invalid_timestamp"]


def test_extreme_timestamp_does_not_abort_healthy_sibling(tmp_path: Path) -> None:
    export_path = _write_export(
        tmp_path / "export.json",
        _export(
            _message(
                _tool_part(part_id="prt_extreme", start=10**4_000),
                _tool_part(part_id="prt_valid", start=1_000),
            )
        ),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert {event.source_refs[0].ref.rsplit("/", maxsplit=1)[-1] for event in result.events} == {
        "prt_extreme",
        "prt_valid",
    }
    extreme_event = next(
        event for event in result.events if event.source_refs[0].ref.endswith("/prt_extreme")
    )
    assert extreme_event.occurred_at is None
    assert [warning.code for warning in result.warnings] == ["invalid_timestamp"]


def test_duplicate_tool_identity_keeps_first_record_and_warns(tmp_path: Path) -> None:
    first = _tool_part(part_id="prt_same", tool="read")
    duplicate = _tool_part(part_id="prt_same", tool="write")
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(first, duplicate)),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert len(result.events) == 1
    assert "read" in result.events[0].summary
    assert [warning.code for warning in result.warnings] == ["duplicate_tool_part"]


def test_multiple_authorized_sessions_are_merged_with_session_provenance(
    tmp_path: Path,
) -> None:
    first_data = _export(
        _message(
            _tool_part(
                part_id="prt_first",
                message_id="msg_first",
                session_id="ses_first",
            ),
            message_id="msg_first",
            session_id="ses_first",
        )
    )
    first_data["info"]["id"] = "ses_first"
    second_data = _export(
        _message(
            _tool_part(
                part_id="prt_second",
                message_id="msg_second",
                session_id="ses_second",
            ),
            message_id="msg_second",
            session_id="ses_second",
        )
    )
    second_data["info"]["id"] = "ses_second"
    first = _write_export(tmp_path / "first.json", first_data)
    second = _write_export(tmp_path / "second.json", second_data)

    result = adapt_opencode_exports(
        (first, second),
        authorized_paths=(first, second),
        project_root=tmp_path,
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 2
    refs = {event.source_refs[0].ref for event in result.events}
    assert any("/ses_first/" in ref for ref in refs)
    assert any("/ses_second/" in ref for ref in refs)


def test_multiple_sessions_require_authorization_for_each_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed = _write_export(tmp_path / "allowed.json", _export(_message(_tool_part())))
    denied = tmp_path / "denied-must-not-be-read.json"
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == denied:
            raise AssertionError("an unauthorized export must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    result = adapt_opencode_exports(
        (allowed, denied),
        authorized_paths=(allowed,),
    )

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["export_not_authorized"]
    assert str(denied) not in result.warnings[0].message


def test_repeated_identical_export_is_deduplicated(tmp_path: Path) -> None:
    export_path = _write_export(tmp_path / "export.json", _export(_message(_tool_part())))

    result = adapt_opencode_exports(
        (export_path, export_path),
        authorized_paths=(export_path,),
    )

    assert len(result.events) == 1
    assert [warning.code for warning in result.warnings] == ["duplicate_export_event"]


def test_ordinary_json_parses_without_hitting_depth_guard(tmp_path: Path) -> None:
    """A normal, shallow export must parse fine — the depth guard is a
    non-intrusive pre-check, not a rejection of everyday records."""
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(_tool_part(), _tool_part(part_id="prt_second"))),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    assert result.status is TraceInputStatus.PARSED
    assert len(result.events) == 2


def test_nesting_depth_guard_rejects_at_one_past_the_limit(tmp_path: Path) -> None:
    """Nesting one level past the guard's limit must be rejected as an
    unsupported export, proving the check trips at its advertised ceiling
    rather than only at the far higher JSON/Python recursion limit."""
    one_past_limit = 129
    over_nesting = "[" * one_past_limit + "0" + "]" * one_past_limit
    over_export = '{"info":{"id":"ses_main","version":"1.18.6"},"messages":' + over_nesting + "}"
    over_path = tmp_path / "one-past-limit.json"
    over_path.write_text(over_export, encoding="utf-8")

    with pytest.raises(UnsupportedOpenCodeFormatError) as caught:
        adapt_opencode_export(over_path, authorized=True)
    assert "嵌套过深" in str(caught.value)


def test_nesting_depth_guard_ignores_braces_inside_strings(tmp_path: Path) -> None:
    """Braces, brackets, escaped quotes, and backslashes inside JSON string
    literals must not be miscounted as nesting. A message whose text repeats
    hundreds of ``{[`` characters, or whose summary carries escaped quotes,
    stays a valid shallow export instead of tripping the depth guard."""
    braces = "{" * 400 + "[" * 400
    pathological_text = '{"braces": "' + braces + '", "escaped": "\\"\\\\{\\["}\n'
    export_path = _write_export(
        tmp_path / "export.json",
        _export(_message(_tool_part(input_data={"filePath": pathological_text}))),
    )

    result = adapt_opencode_export(export_path, authorized=True)

    # The string-laden text is normalized/redacted so the braces never surface
    # in the summary; the export itself must still parse as PARSED, i.e. the
    # depth guard did not mistake the in-string braces for nesting.
    assert result.status is TraceInputStatus.PARSED
    assert "{" not in result.events[0].summary

    # A second, simpler shape: a JSON string value holding escaped quotes and
    # backslashes must leave the counter untouched.
    escaped_only = _write_export(
        tmp_path / "escaped.json",
        _export(_message(_tool_part(input_data={"filePath": '\\"\\\\"'}))),
    )
    assert adapt_opencode_export(escaped_only, authorized=True).status is TraceInputStatus.PARSED
