"""Observe only known LearnTrace outputs and verify final narrative state."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

from learntrace.reporting import (
    load_archive,
    load_payload,
    render_narrative_markdown,
    verify_payload,
)


def artifact_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_SESSION_LOCAL_KINDS = {"narrative_payload", "working_report", "final_report"}


def snapshot_artifact(
    project: Path, session_id: str, item: dict[str, Any]
) -> dict[str, Any] | None:
    """Keep a session-owned copy of project-level outputs.

    Task 4 intentionally writes a few stable project-level paths.  Those paths
    are useful while an analysis is running, but they are not a durable history:
    a later analysis can replace them.  The UI therefore snapshots each observed
    version and records the snapshot path without changing the Skill contract.

    ``None`` means the source changed while it was being copied.  The monitor
    will retry on its next pass instead of associating mixed content with the
    session.
    """

    if item["kind"] in _SESSION_LOCAL_KINDS or item.get("status") == "preexisting":
        return item
    source = (project / str(item["path"])).resolve()
    if not source.is_file() or (project != source and project not in source.parents):
        return None
    suffix = source.suffix or ".bin"
    path_key = hashlib.sha256(str(item["path"]).encode("utf-8")).hexdigest()[:12]
    relative = Path(".learntrace") / "ui-sessions" / session_id / "snapshots"
    relative /= f"{item['kind']}-{path_key}{suffix}"
    destination = project / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as reader, temporary.open("wb") as writer:
            shutil.copyfileobj(reader, writer, length=64 * 1024)
        copied_fingerprint = artifact_fingerprint(temporary)
        if copied_fingerprint != item["fingerprint"]:
            temporary.unlink(missing_ok=True)
            return None
        temporary.replace(destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        return None
    details = {
        **item.get("details", {}),
        "snapshot_path": relative.as_posix(),
        "source_path": item["path"],
    }
    return {**item, "details": details}


def known_artifacts(project: Path, session_id: str) -> list[tuple[str, Path]]:
    session = project / ".learntrace" / "ui-sessions" / session_id
    paths = [
        ("intermediate_record", project / "learning-record.md"),
        ("audit_archive", project / ".learntrace" / "archive-records.json"),
        ("pending_questions", project / ".learntrace" / "learning-questions.md"),
        ("narrative_payload", session / "narrative-payload.json"),
        ("working_report", session / "working-report.md"),
        ("final_report", session / "final-report.md"),
    ]
    registry = project / ".learntrace" / "generated-artifacts.json"
    if registry.is_file():
        try:
            raw = cast(object, json.loads(registry.read_text(encoding="utf-8")))
            entries: object = raw
            if isinstance(raw, dict):
                mapping = cast(dict[str, object], raw)
                entries = mapping.get("artifacts", [])
            iterable = cast(list[object], entries) if isinstance(entries, list) else []
            for entry in iterable:
                value = (
                    cast(dict[str, object], entry).get("path") if isinstance(entry, dict) else entry
                )
                if isinstance(value, str):
                    candidate = (project / value).resolve()
                    if candidate == project or project in candidate.parents:
                        paths.append(("registered_output", candidate))
        except (OSError, ValueError):
            pass
    return paths


def inspect_artifacts(project: Path, session_id: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    archive_path = project / ".learntrace" / "archive-records.json"
    payload_path = project / ".learntrace" / "ui-sessions" / session_id / "narrative-payload.json"
    narrative_valid = False
    violations: list[str] = []
    if archive_path.is_file() and payload_path.is_file():
        try:
            payload = copy.deepcopy(load_payload(payload_path))
            payload["variant"] = "submitted"
            archive = load_archive(archive_path)
            violations = verify_payload(payload, archive)
            final_path = payload_path.with_name("final-report.md")
            if not violations and final_path.is_file():
                expected = render_narrative_markdown(payload, archive)
                narrative_valid = final_path.read_text(encoding="utf-8") == expected
                if not narrative_valid:
                    violations = ["final report does not match the verified submitted payload"]
        except (OSError, ValueError) as error:
            violations = [str(error)]
    seen: set[Path] = set()
    for kind, path in known_artifacts(project, session_id):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        stat = resolved.stat()
        status = "available"
        details: dict[str, Any] = {}
        if kind == "final_report":
            status = "verified" if narrative_valid else "invalid"
            details["violations"] = violations
        result.append(
            {
                "path": resolved.relative_to(project).as_posix(),
                "kind": kind,
                "fingerprint": artifact_fingerprint(resolved),
                "modified_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "status": status,
                "details": details,
            }
        )
    return result
