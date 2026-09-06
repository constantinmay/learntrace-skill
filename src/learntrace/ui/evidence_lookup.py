"""Resolve report citation identifiers without inventing a second evidence reader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


def _find(value: Any, citation_id: str) -> Any | None:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if citation_id in {
            mapping.get("id"),
            mapping.get("event_id"),
            mapping.get("record_id"),
            mapping.get("candidate_id"),
        }:
            return mapping
        for child in mapping.values():
            found = _find(child, citation_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in cast(list[Any], value):
            found = _find(child, citation_id)
            if found is not None:
                return found
    return None


def resolve_citation(
    project: Path,
    session_id: str,
    citation_id: str,
    *,
    archive_path: str | None = None,
) -> dict[str, Any]:
    if not citation_id or len(citation_id) > 512:
        raise ValueError("invalid citation id")
    sources = [
        project / ".learntrace" / "ui-sessions" / session_id / "narrative-payload.json",
        (
            project / archive_path
            if archive_path
            else project / ".learntrace" / "archive-records.json"
        ),
        project / ".learntrace" / "task2-result.json",
        project / ".learntrace" / "task3-result.json",
    ]
    for source in sources:
        if not source.is_file():
            continue
        try:
            payload: Any = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = _find(payload, citation_id)
        if found is not None:
            return {
                "citation_id": citation_id,
                "source": source.relative_to(project).as_posix(),
                "record": found,
                "readback": (
                    "Use the record's revision, path, and line or byte range with the existing "
                    "learntrace git-file/git-evidence commands when source content is needed."
                ),
            }
    raise KeyError(citation_id)
