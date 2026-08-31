"""End-to-end privacy test for the Codex adapter and result writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from learntrace.adapters import adapt_codex_export, write_trace_result
from learntrace.models import ContractValidator

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "codex" / "authorized-session.jsonl"


def test_authorized_artificial_session_is_safely_adapted_and_written(tmp_path: Path) -> None:
    result = adapt_codex_export(FIXTURE_PATH, authorized=True)
    output_path = tmp_path / "trace-result.json"

    write_trace_result(result, output_path)

    payload_value: object = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(payload_value, dict)
    payload = cast("dict[str, object]", payload_value)
    assert payload["status"] == "parsed"
    events_value = payload["events"]
    assert isinstance(events_value, list)
    events = cast("list[object]", events_value)
    assert len(events) == 3
    validator = ContractValidator()
    for event in events:
        validator.validate("observable_event", event)

    serialized = output_path.read_text(encoding="utf-8")
    assert "git status" in serialized
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
    ):
        assert forbidden not in serialized
