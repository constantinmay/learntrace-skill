"""CLI and file-loading entry point for LearnTrace Task 4."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import jsonschema

from learntrace import __version__
from learntrace.artifacts import register_generated_artifacts
from learntrace.models import (
    SCHEMA_VERSION,
    CandidateStatus,
    ConfirmationDecision,
    ContractValidator,
    EventKind,
    LearningNodeCandidate,
    MissingInfo,
    NodeType,
    ObservableEvent,
    RecordType,
    SourceRef,
    SourceType,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.reporting import (
    ArchiveBundle,
    ArchiveWarning,
    CandidateInferencer,
    apply_confirmations,
    archive_manifest,
    build_archive_bundle,
    bundle_to_dict,
    render_markdown,
    render_questions_markdown,
    validate_bundle,
)

JsonObject = dict[str, object]
_IGNORED_DIR_NAMES = frozenset(
    {
        ".eggs",
        ".git",
        ".hg",
        ".learntrace",
        ".mypy_cache",
        ".opencode",
        ".pytest_cache",
        ".pyright",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "env",
        "node_modules",
        "venv",
    }
)
_LEARNTRACE_CONTAINER_KEYS = frozenset({"events", "confirmations", "warnings"})
_LEARNTRACE_BUNDLE_MARKER = "learntrace_bundle"
# A single LearnTrace record is tagged with one of these three evidence levels.
# Anything else carrying an "evidence_level" key is an unrelated JSON document
# and must not be swallowed by the default directory scan.
_LEARNTRACE_EVIDENCE_LEVELS = frozenset(
    {
        ObservableEvent.EVIDENCE_LEVEL,
        StudentConfirmation.EVIDENCE_LEVEL,
        "candidate_inference",
    }
)


@dataclass(frozen=True, slots=True)
class LoadedProjectRecords:
    events: tuple[ObservableEvent, ...]
    confirmations: tuple[StudentConfirmation, ...]
    warnings: tuple[ArchiveWarning, ...] = ()
    task2_meta: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass(frozen=True, slots=True)
class LearningRecordWriteResult:
    output_path: Path
    records_output_path: Path | None
    questions_output_path: Path | None
    archive: dict[str, object]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learntrace",
        description="Build a traceable learning portfolio from local project evidence.",
    )
    parser.add_argument(
        "project_dir",
        nargs="?",
        help="Directory containing LearnTrace JSON records.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Path to the Markdown learning record. Defaults to <project_dir>/learning-record.md.",
    )
    parser.add_argument(
        "--records-output",
        type=Path,
        help="Optional path for machine-readable validated archive JSON.",
    )
    parser.add_argument(
        "--questions-output",
        type=Path,
        help="Optional path for unresolved student confirmation questions.",
    )
    parser.add_argument(
        "--confirmations",
        action="append",
        type=Path,
        default=[],
        help="Explicit student-confirmation JSON file (repeatable).",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Existing archive-records.json whose candidates must be reused.",
    )
    parser.add_argument(
        "--strict-inputs",
        action="store_true",
        help="Fail on unrelated JSON files instead of skipping them.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _iter_json_files(
    root: Path,
    *,
    output_filenames: frozenset[str] = frozenset(),
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_DIR_NAMES)
        for filename in sorted(filenames):
            if not filename.endswith(".json"):
                continue
            if filename in output_filenames:
                continue
            filepath = Path(directory) / filename
            # Skip symlinks to prevent reading files outside the project
            try:
                if filepath.is_symlink():
                    continue
            except OSError:
                continue
            paths.append(filepath)
    return tuple(paths)


def _read_json_object(path: Path, *, strict_inputs: bool) -> JsonObject | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path}: invalid JSON: {exc.msg}"
        raise ValueError(msg) from exc
    if isinstance(data, dict):
        return cast(JsonObject, data)
    if strict_inputs:
        msg = f"{path}: expected a JSON object"
        raise ValueError(msg)
    return None


def _invalid_input_warning(root: Path, path: Path, error: ValueError) -> ArchiveWarning:
    """Build a project-relative warning without repeating an absolute source path."""
    return ArchiveWarning(
        code="invalid_learntrace_input",
        source=path.relative_to(root).as_posix(),
        message=str(error).removeprefix(f"{path}: "),
    )


def _is_object_list(value: object) -> bool:
    if not isinstance(value, list):
        return False
    items = cast(list[object], value)
    return all(isinstance(item, dict) for item in items)


def _looks_like_observable_event_object(data: JsonObject) -> bool:
    return (
        isinstance(data.get("id"), str)
        and isinstance(data.get("kind"), str)
        and isinstance(data.get("summary"), str)
        and isinstance(data.get("source_refs"), list)
    )


def _looks_like_confirmation_object(data: JsonObject) -> bool:
    return (
        data.get("evidence_level") == StudentConfirmation.EVIDENCE_LEVEL
        and isinstance(data.get("id"), str)
        and isinstance(data.get("candidate_id"), str)
        and isinstance(data.get("decision"), str)
        and "student_statement" in data
    )


def _looks_like_learntrace_container(data: JsonObject) -> bool:
    # The events list must be non-empty and every element a well-shaped
    # observable event. An empty "events" is not proof of LearnTrace data — a
    # plain business JSON like {"events": []} must not be treated as a
    # LearnTrace container (see "default scan misreading ordinary JSON").
    events = data.get("events")
    event_objects = cast(list[JsonObject], events) if _is_object_list(events) else []
    confirmations = data.get("confirmations")
    confirmation_objects = (
        cast(list[JsonObject], confirmations) if _is_object_list(confirmations) else []
    )
    has_valid_events = bool(event_objects) and all(
        _looks_like_observable_event_object(item) for item in event_objects
    )
    has_valid_confirmations = bool(confirmation_objects) and all(
        _looks_like_confirmation_object(item) for item in confirmation_objects
    )
    if not has_valid_events and not has_valid_confirmations:
        return False
    if events is not None and not _is_object_list(events):
        return False
    if confirmations is not None and not _is_object_list(confirmations):
        return False

    warnings = data.get("warnings")
    return warnings is None or _is_object_list(warnings)


def _looks_like_generated_archive_json(data: JsonObject) -> bool:
    # Recognize both the current (marker-tagged) archive and older snapshots
    # produced before ``learntrace_bundle`` existed. Older snapshots lack the
    # marker but still carry the archive-only shape (``archive_manifest``,
    # ``record_counts``, ``archive_version``) that a Task2 ``parse-result.json``
    # or a teacher-authored foreign JSON never has. Treating both as generated
    # keeps a stale ``out/archive-records.json`` from being re-ingested as input
    # and colliding with the authoritative parse-result events.
    has_archive_shape = (
        "archive_manifest" in data and "record_counts" in data and "archive_version" in data
    )
    if not has_archive_shape:
        return False
    if data.get(_LEARNTRACE_BUNDLE_MARKER) is True:
        return True
    return "events" in data and "candidates" in data


def _looks_like_learntrace_json(data: JsonObject) -> bool:
    evidence_level = data.get("evidence_level")
    if evidence_level is not None:
        # A single record is only LearnTrace data when its evidence level is one
        # of the three known values; a foreign JSON that happens to carry an
        # "evidence_level" key (e.g. a business object) must not be swallowed.
        return evidence_level in _LEARNTRACE_EVIDENCE_LEVELS
    if _looks_like_generated_archive_json(data):
        return False
    if not (_LEARNTRACE_CONTAINER_KEYS & set(data.keys())):
        return False
    return _looks_like_learntrace_container(data)


def _validate_record(
    validator: ContractValidator,
    record_type: RecordType,
    record: JsonObject,
    path: Path,
) -> None:
    try:
        validator.validate(record_type, record)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        suffix = f" at {location}" if location else ""
        msg = f"{path}: invalid {record_type}{suffix}: {exc.message}"
        raise ValueError(msg) from exc


def _require_string(data: JsonObject, key: str, path: Path) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"{path}: expected string field {key!r}"
        raise ValueError(msg)
    return value


def _optional_string(data: JsonObject, key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{path}: expected optional string field {key!r}"
        raise ValueError(msg)
    return value


def _require_list(data: JsonObject, key: str, path: Path) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        msg = f"{path}: expected list field {key!r}"
        raise ValueError(msg)
    return cast(list[object], value)


def _require_object(value: object, label: str, path: Path) -> JsonObject:
    if not isinstance(value, dict):
        msg = f"{path}: expected object for {label}"
        raise ValueError(msg)
    return cast(JsonObject, value)


def _parse_source_ref(raw: object, path: Path) -> SourceRef:
    data = _require_object(raw, "source_ref", path)
    return SourceRef(
        type=SourceType(_require_string(data, "type", path)),
        ref=_require_string(data, "ref", path),
        note=_optional_string(data, "note", path),
    )


def _parse_text_or_missing(value: object, path: Path, field_name: str) -> TextOrMissing:
    if isinstance(value, str):
        return value
    data = _require_object(value, field_name, path)
    status = _require_string(data, "status", path)
    if status != "not_recorded":
        msg = f"{path}: unsupported missing status {status!r}"
        raise ValueError(msg)
    return MissingInfo(note=_optional_string(data, "note", path))


def _parse_event(raw: JsonObject, path: Path) -> ObservableEvent:
    source_refs = tuple(
        _parse_source_ref(item, path) for item in _require_list(raw, "source_refs", path)
    )
    return ObservableEvent(
        id=_require_string(raw, "id", path),
        kind=EventKind(_require_string(raw, "kind", path)),
        summary=_require_string(raw, "summary", path),
        source_refs=source_refs,
        occurred_at=_optional_string(raw, "occurred_at", path),
    )


def _parse_confirmation(raw: JsonObject, path: Path) -> StudentConfirmation:
    student_statement_raw = raw.get("student_statement")
    if student_statement_raw is None:
        msg = f"{path}: missing student_statement"
        raise ValueError(msg)
    return StudentConfirmation(
        id=_require_string(raw, "id", path),
        candidate_id=_require_string(raw, "candidate_id", path),
        decision=ConfirmationDecision(_require_string(raw, "decision", path)),
        student_statement=_parse_text_or_missing(student_statement_raw, path, "student_statement"),
        confirmed_at=_optional_string(raw, "confirmed_at", path),
    )


def _parse_candidate(raw: JsonObject, path: Path) -> LearningNodeCandidate:
    basis_items = _require_list(raw, "basis_event_ids", path)
    if not all(isinstance(item, str) for item in basis_items):
        msg = f"{path}: expected string items in 'basis_event_ids'"
        raise ValueError(msg)
    return LearningNodeCandidate(
        id=_require_string(raw, "id", path),
        node_type=NodeType(_require_string(raw, "node_type", path)),
        statement=_require_string(raw, "statement", path),
        basis_event_ids=tuple(cast(list[str], basis_items)),
        uncertainty=_require_string(raw, "uncertainty", path),
        question_to_student=_parse_text_or_missing(
            raw.get("question_to_student"),
            path,
            "question_to_student",
        ),
        status=CandidateStatus(_require_string(raw, "status", path)),
    )


def _confirmation_id(data: JsonObject) -> str:
    content = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"conf-{hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]}"


def _parse_explicit_confirmation(
    raw: JsonObject,
    path: Path,
    validator: ContractValidator,
) -> StudentConfirmation:
    if raw.get("evidence_level") == StudentConfirmation.EVIDENCE_LEVEL:
        if raw.get("confirmed_at") is None:
            msg = f"{path}: explicit confirmation requires confirmed_at"
            raise ValueError(msg)
        _validate_record(validator, "student_confirmation", raw, path)
        return _parse_confirmation(raw, path)

    allowed = {"candidate_id", "confirmed_at", "decision", "id", "student_statement"}
    unexpected = sorted(set(raw) - allowed)
    if unexpected:
        msg = f"{path}: unexpected confirmation fields: {', '.join(unexpected)}"
        raise ValueError(msg)
    candidate_id = _require_string(raw, "candidate_id", path)
    decision = _require_string(raw, "decision", path)
    confirmed_at = _require_string(raw, "confirmed_at", path)
    statement_raw = raw.get("student_statement", {"status": "not_recorded"})
    normalized: JsonObject = {
        "schema_version": SCHEMA_VERSION,
        "evidence_level": StudentConfirmation.EVIDENCE_LEVEL,
        "candidate_id": candidate_id,
        "decision": decision,
        "student_statement": statement_raw,
        "confirmed_at": confirmed_at,
    }
    record_id = raw.get("id")
    if record_id is not None and not isinstance(record_id, str):
        msg = f"{path}: expected optional string field 'id'"
        raise ValueError(msg)
    normalized["id"] = record_id if record_id is not None else _confirmation_id(normalized)
    _validate_record(validator, "student_confirmation", normalized, path)
    return _parse_confirmation(normalized, path)


def load_confirmation_file(
    path: Path,
    *,
    validator: ContractValidator | None = None,
) -> tuple[StudentConfirmation, ...]:
    """Load an explicitly supplied full or shorthand confirmation file."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        msg = f"{resolved} is not a file"
        raise FileNotFoundError(msg)
    raw = _read_json_object(resolved, strict_inputs=True)
    if raw is None:  # pragma: no cover - strict object loading cannot return None
        raise ValueError(f"{resolved}: expected a JSON object")
    if raw.get("evidence_level") == StudentConfirmation.EVIDENCE_LEVEL:
        entries = [raw]
    else:
        entries = [
            _require_object(item, f"confirmations[{index}]", resolved)
            for index, item in enumerate(_require_list(raw, "confirmations", resolved), start=1)
        ]
        if not entries:
            msg = f"{resolved}: confirmations must not be empty"
            raise ValueError(msg)
    contract = validator if validator is not None else ContractValidator()
    return tuple(_parse_explicit_confirmation(entry, resolved, contract) for entry in entries)


def load_archive_snapshot(
    path: Path,
    *,
    validator: ContractValidator | None = None,
) -> ArchiveBundle:
    """Load the exact candidates and evidence produced by a previous run."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        msg = f"analysis snapshot does not exist: {resolved}; run LearnTrace once before confirming"
        raise FileNotFoundError(msg)
    raw = _read_json_object(resolved, strict_inputs=True)
    if raw is None or not _looks_like_generated_archive_json(raw):
        msg = f"{resolved}: expected a generated LearnTrace archive snapshot"
        raise ValueError(msg)

    contract = validator if validator is not None else ContractValidator()
    events: list[ObservableEvent] = []
    for index, item in enumerate(_require_list(raw, "events", resolved), start=1):
        event = _require_object(item, f"events[{index}]", resolved)
        _validate_record(contract, "observable_event", event, resolved)
        events.append(_parse_event(event, resolved))

    candidates: list[LearningNodeCandidate] = []
    for index, item in enumerate(_require_list(raw, "candidates", resolved), start=1):
        candidate = _require_object(item, f"candidates[{index}]", resolved)
        _validate_record(contract, "learning_node_candidate", candidate, resolved)
        candidates.append(_parse_candidate(candidate, resolved))

    confirmations: list[StudentConfirmation] = []
    for index, item in enumerate(_require_list(raw, "confirmations", resolved), start=1):
        confirmation = _require_object(item, f"confirmations[{index}]", resolved)
        _validate_record(contract, "student_confirmation", confirmation, resolved)
        confirmations.append(_parse_confirmation(confirmation, resolved))

    warnings = tuple(
        _parse_warning(item, resolved) for item in _require_list(raw, "warnings", resolved)
    )
    inference_mode = _require_string(raw, "candidate_inference_mode", resolved)
    task2_meta: dict[str, Any] = {}
    manifest_raw = raw.get("archive_manifest")
    if isinstance(manifest_raw, dict):
        manifest = cast(JsonObject, manifest_raw)
        task2_meta_raw = manifest.get("task2_meta")
        if isinstance(task2_meta_raw, dict):
            task2_meta = dict(cast(dict[str, Any], task2_meta_raw))

    bundle = ArchiveBundle(
        events=tuple(events),
        candidates=tuple(candidates),
        confirmations=tuple(confirmations),
        warnings=warnings,
        inference_mode=inference_mode,
        task2_meta=task2_meta,
    )
    validate_bundle(bundle, validator=contract)
    stored_manifest = _require_object(raw.get("archive_manifest"), "archive_manifest", resolved)
    stored_fingerprint = _require_string(stored_manifest, "content_fingerprint", resolved)
    computed_fingerprint = cast(str, archive_manifest(bundle)["content_fingerprint"])
    if stored_fingerprint != computed_fingerprint:
        msg = f"{resolved}: analysis snapshot fingerprint does not match its records"
        raise ValueError(msg)
    return bundle


def _parse_warning(raw: object, path: Path) -> ArchiveWarning:
    data = _require_object(raw, "warning", path)
    # Task 2's ParseWarning and this task's ArchiveWarning both serialize the
    # origin field as "source", but Task 3's TraceParseIssue serializes it as
    # "location". Accept both so a Task 3 result carrying warnings loads
    # instead of failing on a missing "source" key.
    source = data.get("source")
    if not isinstance(source, str):
        source = data.get("location")
    if not isinstance(source, str):
        msg = f"{path}: expected string field 'source' or 'location'"
        raise ValueError(msg)
    return ArchiveWarning(
        code=_require_string(data, "code", path),
        source=source,
        message=_require_string(data, "message", path),
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            temporary_path = Path(handle.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _validate_distinct_output_paths(
    markdown_path: Path,
    records_path: Path | None,
    questions_path: Path | None,
) -> None:
    seen: dict[Path, str] = {}
    for label, path in (
        ("Markdown", markdown_path),
        ("archive JSON", records_path),
        ("pending questions", questions_path),
    ):
        if path is None:
            continue
        resolved = path.expanduser().resolve()
        existing_label = seen.get(resolved)
        if existing_label is not None:
            msg = f"{label} output path conflicts with {existing_label} output path: {path}"
            raise ValueError(msg)
        seen[resolved] = label


def _collect_records_from_json(
    raw: JsonObject,
    path: Path,
    validator: ContractValidator,
) -> tuple[
    list[ObservableEvent],
    list[StudentConfirmation],
    list[ArchiveWarning],
    dict[str, object],
]:
    events: list[ObservableEvent] = []
    confirmations: list[StudentConfirmation] = []
    warnings: list[ArchiveWarning] = []

    task2_meta: dict[str, object] = {}
    for key in ("parser_version", "analysis_scope", "inventory"):
        value = raw.get(key)
        if value is not None:
            task2_meta[key] = value
    evidence_level = raw.get("evidence_level")
    if evidence_level == ObservableEvent.EVIDENCE_LEVEL:
        _validate_record(validator, "observable_event", raw, path)
        events.append(_parse_event(raw, path))
    elif evidence_level == StudentConfirmation.EVIDENCE_LEVEL:
        _validate_record(validator, "student_confirmation", raw, path)
        confirmations.append(_parse_confirmation(raw, path))

    raw_events = raw.get("events")
    if raw_events is not None:
        for index, item in enumerate(_require_list(raw, "events", path), start=1):
            event = _require_object(item, f"events[{index}]", path)
            _validate_record(validator, "observable_event", event, path)
            events.append(_parse_event(event, path))

    raw_confirmations = raw.get("confirmations")
    if raw_confirmations is not None:
        for index, item in enumerate(_require_list(raw, "confirmations", path), start=1):
            confirmation = _require_object(item, f"confirmations[{index}]", path)
            _validate_record(validator, "student_confirmation", confirmation, path)
            confirmations.append(_parse_confirmation(confirmation, path))

    raw_warnings = raw.get("warnings")
    if raw_warnings is not None:
        warnings.extend(_parse_warning(item, path) for item in _require_list(raw, "warnings", path))

    return events, confirmations, warnings, task2_meta


def load_project_artifacts(
    project_dir: Path,
    *,
    validator: ContractValidator | None = None,
    strict_inputs: bool = False,
) -> LoadedProjectRecords:
    root = project_dir.resolve()
    if not root.is_dir():
        msg = f"{root} is not a directory"
        raise FileNotFoundError(msg)

    contract_validator = validator if validator is not None else ContractValidator()
    events: list[ObservableEvent] = []
    confirmations: list[StudentConfirmation] = []
    warnings: list[ArchiveWarning] = []
    task2_meta: dict[str, object] = {}
    event_sources: dict[str, tuple[ObservableEvent, Path]] = {}
    confirmation_sources: dict[str, tuple[StudentConfirmation, Path]] = {}

    for path in _iter_json_files(root):
        try:
            raw = _read_json_object(path, strict_inputs=strict_inputs)
        except ValueError as exc:
            if strict_inputs:
                raise
            warnings.append(_invalid_input_warning(root, path, exc))
            continue
        if raw is None:
            continue
        if _looks_like_generated_archive_json(raw):
            continue
        if not _looks_like_learntrace_json(raw):
            if strict_inputs:
                msg = f"{path}: does not look like a LearnTrace JSON record"
                raise ValueError(msg)
            continue
        try:
            (
                loaded_events,
                loaded_confirmations,
                loaded_warnings,
                file_meta,
            ) = _collect_records_from_json(
                raw,
                path,
                contract_validator,
            )
        except ValueError as exc:
            if strict_inputs:
                raise
            warnings.append(_invalid_input_warning(root, path, exc))
            continue
        task2_meta.update(file_meta)
        for event in loaded_events:
            existing = event_sources.get(event.id)
            if existing is None:
                event_sources[event.id] = (event, path)
                events.append(event)
                continue
            existing_event, existing_path = existing
            if existing_event.to_dict() != event.to_dict():
                msg = (
                    f"conflicting observable_event records for id {event.id}: "
                    f"{existing_path} vs {path}"
                )
                raise ValueError(msg)
        for confirmation in loaded_confirmations:
            existing = confirmation_sources.get(confirmation.id)
            if existing is None:
                confirmation_sources[confirmation.id] = (confirmation, path)
                confirmations.append(confirmation)
                continue
            existing_confirmation, existing_path = existing
            if existing_confirmation.to_dict() != confirmation.to_dict():
                msg = (
                    f"conflicting student_confirmation records for id {confirmation.id}: "
                    f"{existing_path} vs {path}"
                )
                raise ValueError(msg)
        warnings.extend(loaded_warnings)

    if not events and not confirmations:
        msg = (
            f"no observable_event records found under {root}; "
            "expected LearnTrace record JSON or a Task2 parse-result JSON with an events list"
        )
        raise ValueError(msg)

    return LoadedProjectRecords(
        events=tuple(events),
        confirmations=tuple(confirmations),
        warnings=tuple(warnings),
        task2_meta=task2_meta,
    )


def load_project_records(
    project_dir: Path,
    *,
    validator: ContractValidator | None = None,
    strict_inputs: bool = False,
) -> tuple[tuple[ObservableEvent, ...], tuple[StudentConfirmation, ...]]:
    loaded = load_project_artifacts(project_dir, validator=validator, strict_inputs=strict_inputs)
    return loaded.events, loaded.confirmations


def write_learning_record_result(
    project_dir: Path,
    *,
    output_path: Path | None = None,
    records_output_path: Path | None = None,
    questions_output_path: Path | None = None,
    validator: ContractValidator | None = None,
    inferencer: CandidateInferencer | None = None,
    strict_inputs: bool = False,
    confirmation_paths: Iterable[Path] = (),
    snapshot_path: Path | None = None,
    artifact_registry_root: Path | None = None,
) -> LearningRecordWriteResult:
    contract_validator = validator if validator is not None else ContractValidator()
    explicit_confirmations = tuple(
        confirmation
        for path in confirmation_paths
        for confirmation in load_confirmation_file(path, validator=contract_validator)
    )
    if snapshot_path is not None:
        snapshot = load_archive_snapshot(snapshot_path, validator=contract_validator)
        bundle = apply_confirmations(
            snapshot,
            explicit_confirmations,
            validator=contract_validator,
        )
    else:
        loaded = load_project_artifacts(
            project_dir,
            validator=contract_validator,
            strict_inputs=strict_inputs,
        )
        if not loaded.events:
            msg = (
                f"no observable_event records found under {project_dir.resolve()}; "
                "expected LearnTrace record JSON or a Task2 parse-result JSON with an events list"
            )
            raise ValueError(msg)
        bundle = build_archive_bundle(
            loaded.events,
            confirmations=(*loaded.confirmations, *explicit_confirmations),
            warnings=loaded.warnings,
            validator=contract_validator,
            inferencer=inferencer,
            task2_meta=loaded.task2_meta,
        )
    markdown = render_markdown(bundle, source_dir=project_dir.resolve())
    destination = (
        output_path if output_path is not None else project_dir.resolve() / "learning-record.md"
    )
    _validate_distinct_output_paths(destination, records_output_path, questions_output_path)
    archive = bundle_to_dict(bundle, validator=contract_validator)
    _write_text(destination, markdown)
    if records_output_path is not None:
        archive_json = json.dumps(
            archive,
            ensure_ascii=False,
            indent=2,
        )
        _write_text(records_output_path, f"{archive_json}\n")
    if questions_output_path is not None:
        _write_text(questions_output_path, render_questions_markdown(bundle))
    if artifact_registry_root is not None:
        register_generated_artifacts(
            artifact_registry_root,
            tuple(
                path
                for path in (destination, records_output_path, questions_output_path)
                if path is not None
            ),
        )
    return LearningRecordWriteResult(
        output_path=destination,
        records_output_path=records_output_path,
        questions_output_path=questions_output_path,
        archive=archive,
    )


def write_learning_record(
    project_dir: Path,
    *,
    output_path: Path | None = None,
    records_output_path: Path | None = None,
    questions_output_path: Path | None = None,
    validator: ContractValidator | None = None,
    inferencer: CandidateInferencer | None = None,
    strict_inputs: bool = False,
    confirmation_paths: Iterable[Path] = (),
    snapshot_path: Path | None = None,
) -> Path:
    result = write_learning_record_result(
        project_dir,
        output_path=output_path,
        records_output_path=records_output_path,
        questions_output_path=questions_output_path,
        validator=validator,
        inferencer=inferencer,
        strict_inputs=strict_inputs,
        confirmation_paths=confirmation_paths,
        snapshot_path=snapshot_path,
    )
    return result.output_path


def _format_cli_summary(result: LearningRecordWriteResult) -> str:
    record_counts = cast(dict[str, object], result.archive["record_counts"])
    manifest = cast(dict[str, object], result.archive["archive_manifest"])
    hash_algorithm = cast(str, manifest["hash_algorithm"])
    content_fingerprint = cast(str, manifest["content_fingerprint"])
    lines = [
        f"Wrote learning record: {result.output_path}",
        f"Archive fingerprint: {hash_algorithm}:{content_fingerprint}",
        (
            "Record counts: "
            f"observable_fact={record_counts['observable_fact']}, "
            f"candidate_inference={record_counts['candidate_inference']}, "
            f"student_confirmation={record_counts['student_confirmation']}, "
            f"pending_questions={record_counts['pending_questions']}"
        ),
    ]
    if result.records_output_path is not None:
        lines.append(f"Wrote archive JSON: {result.records_output_path}")
    if result.questions_output_path is not None:
        lines.append(f"Wrote pending questions: {result.questions_output_path}")
    return "\n".join(lines)


def main(argv: list[str] | tuple[str, ...] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.project_dir is None:
        parser.error("the following arguments are required: project_dir")

    project_dir = Path(args.project_dir)
    try:
        result = write_learning_record_result(
            project_dir,
            output_path=args.output,
            records_output_path=args.records_output,
            questions_output_path=args.questions_output,
            strict_inputs=args.strict_inputs,
            confirmation_paths=tuple(args.confirmations),
            snapshot_path=args.snapshot,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
    print(_format_cli_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
