from __future__ import annotations

from pathlib import Path

from learntrace.ui.artifact_monitor import inspect_artifacts


def test_intermediate_record_is_not_a_final_report(tmp_path: Path) -> None:
    (tmp_path / "learning-record.md").write_text("# intermediate", encoding="utf-8")
    artifacts = inspect_artifacts(tmp_path, "session")
    assert artifacts[0]["kind"] == "intermediate_record"
    assert not any(item["kind"] == "final_report" for item in artifacts)
