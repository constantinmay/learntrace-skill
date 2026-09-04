from pathlib import Path

import pytest

from learntrace.ui.conversation_sources import task3_prompt, validate_source


def test_task3_source_maps_to_existing_exact_path_flags(tmp_path: Path) -> None:
    export = tmp_path / "session.jsonl"
    export.write_text("{}\n", encoding="utf-8")

    source = validate_source("claude-code", str(export))
    prompt = task3_prompt([source])

    assert source["authorization"] == "task3_parse"
    assert "--claude-code-export" in prompt
    assert "--authorize-claude-code-export" in prompt
    assert str(export.resolve()) in prompt


def test_current_ui_session_cannot_be_imported_as_history(tmp_path: Path) -> None:
    export = tmp_path / ".learntrace" / "ui-sessions" / "current" / "session.jsonl"
    export.parent.mkdir(parents=True)
    export.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="当前产品会话"):
        validate_source("codex", str(export))


def test_missing_conversations_are_an_explicit_evidence_gap() -> None:
    assert "不得推断 AI 使用情况" in task3_prompt([])
