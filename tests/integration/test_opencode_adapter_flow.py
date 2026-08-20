"""End-to-end privacy test for the OpenCode adapter and result writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from learntrace.adapters import (
    TraceInputStatus,
    adapt_opencode_export,
    write_trace_result,
)
from learntrace.archive import load_project_artifacts
from learntrace.models import ContractValidator


def test_authorized_artificial_export_is_safely_adapted_and_written(tmp_path: Path) -> None:
    fixture_path = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(
        fixture_path,
        authorized=True,
        project_root=Path("D:/repo"),
    )
    output_path = tmp_path / "trace-result.json"

    write_trace_result(result, output_path)

    payload_value: object = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(payload_value, dict)
    payload = cast("dict[str, object]", payload_value)
    assert payload["status"] == "parsed"
    events_value = payload["events"]
    warnings_value = payload["warnings"]
    assert isinstance(events_value, list)
    assert isinstance(warnings_value, list)
    events = cast("list[object]", events_value)
    warnings = cast("list[object]", warnings_value)
    assert len(events) == 3
    assert warnings == [
        {
            "code": "invalid_tool_part",
            "location": "messages[0].parts[4]",
            "message": "工具记录结构无效，已跳过。",
        }
    ]
    validator = ContractValidator()
    for event in events:
        validator.validate("observable_event", event)

    serialized = output_path.read_text(encoding="utf-8")
    assert "src/app.py" in serialized
    assert "git status" in serialized
    for forbidden in (
        "FORBIDDEN_SESSION_TITLE",
        "FORBIDDEN_CHAT_TEXT",
        "FORBIDDEN_ERROR_BODY",
        "FORBIDDEN_READ_OUTPUT",
        "FORBIDDEN_TOKEN",
        "FORBIDDEN_COMMAND_TAIL",
        "FORBIDDEN_BASH_OUTPUT",
        "FORBIDDEN_TASK_DESCRIPTION",
        "FORBIDDEN_SUBAGENT_TYPE",
        "FORBIDDEN_TASK_PROMPT",
        "FORBIDDEN_TASK_OUTPUT",
        "FORBIDDEN_MALFORMED_CONTENT",
        "FORBIDDEN_MALFORMED_OUTPUT",
        "Users",
        "Fixture Person",
        "TOKEN=",
        "--short",
    ):
        assert forbidden not in serialized

    no_tool_path = tmp_path / "no-tool.json"
    no_tool_path.write_text(
        json.dumps(
            {
                "info": {"id": "ses_empty", "version": "1.18.6"},
                "messages": [
                    {
                        "info": {
                            "id": "msg_empty",
                            "sessionID": "ses_empty",
                            "role": "assistant",
                        },
                        "parts": [
                            {
                                "id": "prt_text",
                                "type": "text",
                                "text": "FORBIDDEN_EMPTY_CHAT",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    no_tool_result = adapt_opencode_export(no_tool_path, authorized=True)

    assert no_tool_result.status is TraceInputStatus.AUTHORIZED_NOT_FOUND
    assert no_tool_result.events == ()


def test_trace_result_warnings_are_loaded_by_task4_loader(tmp_path: Path) -> None:
    """Task 3 serializes a warning's origin field as ``location``, but Task 4's
    loader models it as ``source``. A written Task 3 result carrying a warning
    must still load into the archive pipeline instead of failing on a missing
    ``source`` key."""
    fixture_path = Path(__file__).parents[1] / "fixtures" / "opencode" / "authorized-export.json"
    result = adapt_opencode_export(
        fixture_path,
        authorized=True,
        project_root=Path("D:/repo"),
    )
    output_path = tmp_path / "trace-result.json"
    write_trace_result(result, output_path)

    loaded = load_project_artifacts(tmp_path)

    assert len(loaded.events) == 3
    assert loaded.warnings and loaded.warnings[0].code == "invalid_tool_part"
    assert loaded.warnings[0].source == "messages[0].parts[4]"
