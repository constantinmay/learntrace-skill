from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from learntrace.parsers import (
    discover_static_materials,
    parse_static_materials,
    write_parse_result,
)

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "static_parser"


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=env,
    )


def _commit(root: Path, message: str, timestamp: str) -> None:
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = timestamp
    commit_env["GIT_COMMITTER_DATE"] = timestamp
    _git(root, "commit", "-q", "-m", message, env=commit_env)


def _make_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "project"
    shutil.copytree(FIXTURE_ROOT / "project", repository)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Fixture User")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "add", ".")
    _commit(repository, "add static parser fixture", "2026-07-20T10:00:00+08:00")
    parser = repository / "src" / "parser.py"
    added_function = (
        "\ndef required_columns() -> tuple[str, ...]:\n    return ('name', 'credits')\n"
    )
    parser.write_text(
        parser.read_text(encoding="utf-8") + added_function,
        encoding="utf-8",
    )
    _git(repository, "add", "src/parser.py")
    _commit(repository, "record required columns", "2026-07-21T11:00:00+08:00")
    return repository


def _load_expected() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_ROOT / "expected-results.json").read_text(encoding="utf-8")),
    )


def test_static_parser_fixed_project_end_to_end(tmp_path: Path) -> None:
    repository = _make_repository(tmp_path)
    expected = _load_expected()
    materials = discover_static_materials(repository)

    first = parse_static_materials(
        repository,
        document_paths=materials.documents,
        test_log_paths=materials.test_logs,
        include_git=materials.has_git,
    )
    second = parse_static_materials(
        repository,
        document_paths=materials.documents,
        test_log_paths=materials.test_logs,
        include_git=materials.has_git,
    )
    first_data = first.to_dict()

    assert first_data == second.to_dict()
    assert first.inventory is not None
    inventory = first.inventory.to_dict()
    for key, value in expected["inventory"].items():
        assert inventory[key] == value
    assert [warning.code for warning in first.warnings] == expected["warning_codes"]
    assert {event.kind.value for event in first.events} == set(expected["required_event_kinds"])
    assert len(first.events) >= expected["minimum_event_count"]
    assert len({event.id for event in first.events}) == len(first.events)
    assert any("修改文件 src/parser.py" in event.summary for event in first.events)
    assert any("tests/parser_checks.py::test_timeout" in event.summary for event in first.events)
    assert not (repository / "execution-marker.txt").exists()

    output = tmp_path / "parsed-result.json"
    write_parse_result(first, output)
    assert json.loads(output.read_text(encoding="utf-8")) == first_data
