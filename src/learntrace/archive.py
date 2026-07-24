"""CLI and file-loading entry point for LearnTrace Task 4."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import jsonschema

from learntrace import __version__
from learntrace.models import (
    ConfirmationDecision,
    ContractValidator,
    EventKind,
    MissingInfo,
    ObservableEvent,
    RecordType,
    SourceRef,
    SourceType,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.reporting import (
    ArchiveWarning,
    CandidateInferencer,
    build_archive_bundle,
    bundle_to_dict,
    render_markdown,
    render_questions_markdown,
)

JsonObject = dict[str, object]
_IGNORED_DIR_NAMES = frozenset(
    {
        ".eggs",
        ".git",
        ".hg",
        ".mypy_cache",
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


@dataclass(frozen=True, slots=True)
class LoadedProjectRecords:
    events: tuple[ObservableEvent, ...]
    confirmations: tuple[StudentConfirmation, ...]
    warnings: tuple[ArchiveWarning, ...] = ()


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
        "--strict-inputs",
        action="store_true",
        help="Fail on unrelated JSON files instead of skipping them.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _iter_json_files(root: Path) -> tuple[Path, ...]:
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _IGNORED_DIR_NAMES)
        for filename in sorted(filenames):
            if filename.endswith(".json"):
                paths.append(Path(directory) / filename)
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


def _looks_like_learntrace_json(data: JsonObject) -> bool:
    return data.get("evidence_level") is not None or any(
        key in data for key in _LEARNTRACE_CONTAINER_KEYS
    )


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


def _parse_warning(raw: object, path: Path) -> ArchiveWarning:
    data = _require_object(raw, "warning", path)
    return ArchiveWarning(
        code=_require_string(data, "code", path),
        source=_require_string(data, "source", path),
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
) -> tuple[list[ObservableEvent], list[StudentConfirmation], list[ArchiveWarning]]:
    events: list[ObservableEvent] = []
    confirmations: list[StudentConfirmation] = []
    warnings: list[ArchiveWarning] = []
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

    return events, confirmations, warnings


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

    for path in _iter_json_files(root):
        raw = _read_json_object(path, strict_inputs=strict_inputs)
        if raw is None:
            continue
        if not _looks_like_learntrace_json(raw):
            if strict_inputs:
                msg = f"{path}: does not look like a LearnTrace JSON record"
                raise ValueError(msg)
            continue
        loaded_events, loaded_confirmations, loaded_warnings = _collect_records_from_json(
            raw,
            path,
            contract_validator,
        )
        events.extend(loaded_events)
        confirmations.extend(loaded_confirmations)
        warnings.extend(loaded_warnings)

    if not events:
        msg = (
            f"no observable_event records found under {root}; "
            "expected LearnTrace record JSON or a Task2 parse-result JSON with an events list"
        )
        raise ValueError(msg)

    return LoadedProjectRecords(
        events=tuple(events),
        confirmations=tuple(confirmations),
        warnings=tuple(warnings),
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
) -> LearningRecordWriteResult:
    contract_validator = validator if validator is not None else ContractValidator()
    loaded = load_project_artifacts(
        project_dir,
        validator=contract_validator,
        strict_inputs=strict_inputs,
    )
    bundle = build_archive_bundle(
        loaded.events,
        confirmations=loaded.confirmations,
        warnings=loaded.warnings,
        validator=contract_validator,
        inferencer=inferencer,
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
) -> Path:
    result = write_learning_record_result(
        project_dir,
        output_path=output_path,
        records_output_path=records_output_path,
        questions_output_path=questions_output_path,
        validator=validator,
        inferencer=inferencer,
        strict_inputs=strict_inputs,
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
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
    print(_format_cli_summary(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
