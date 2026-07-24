"""CLI and file-loading entry point for LearnTrace Task 4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from learntrace import __version__
from learntrace.models import (
    ConfirmationDecision,
    ContractValidator,
    EventKind,
    MissingInfo,
    ObservableEvent,
    SourceRef,
    SourceType,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.reporting import (
    CandidateInferencer,
    build_archive_bundle,
    render_markdown,
)

JsonObject = dict[str, object]


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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _read_json_object(path: Path) -> JsonObject:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{path} does not contain a JSON object"
        raise ValueError(msg)
    return cast(JsonObject, data)


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
        _parse_source_ref(item, path)
        for item in _require_list(raw, "source_refs", path)
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


def _collect_records_from_json(
    raw: JsonObject,
    path: Path,
    validator: ContractValidator,
) -> tuple[list[ObservableEvent], list[StudentConfirmation]]:
    events: list[ObservableEvent] = []
    confirmations: list[StudentConfirmation] = []
    evidence_level = raw.get("evidence_level")
    if evidence_level == ObservableEvent.EVIDENCE_LEVEL:
        validator.validate("observable_event", raw)
        events.append(_parse_event(raw, path))
    elif evidence_level == StudentConfirmation.EVIDENCE_LEVEL:
        validator.validate("student_confirmation", raw)
        confirmations.append(_parse_confirmation(raw, path))

    raw_events = raw.get("events")
    if raw_events is not None:
        for index, item in enumerate(_require_list(raw, "events", path), start=1):
            event = _require_object(item, f"events[{index}]", path)
            validator.validate("observable_event", event)
            events.append(_parse_event(event, path))

    raw_confirmations = raw.get("confirmations")
    if raw_confirmations is not None:
        for index, item in enumerate(_require_list(raw, "confirmations", path), start=1):
            confirmation = _require_object(item, f"confirmations[{index}]", path)
            validator.validate("student_confirmation", confirmation)
            confirmations.append(_parse_confirmation(confirmation, path))

    return events, confirmations


def load_project_records(
    project_dir: Path,
    *,
    validator: ContractValidator | None = None,
) -> tuple[tuple[ObservableEvent, ...], tuple[StudentConfirmation, ...]]:
    root = project_dir.resolve()
    if not root.is_dir():
        msg = f"{root} is not a directory"
        raise FileNotFoundError(msg)

    contract_validator = validator if validator is not None else ContractValidator()
    events: list[ObservableEvent] = []
    confirmations: list[StudentConfirmation] = []

    for path in sorted(root.rglob("*.json")):
        raw = _read_json_object(path)
        loaded_events, loaded_confirmations = _collect_records_from_json(
            raw,
            path,
            contract_validator,
        )
        events.extend(loaded_events)
        confirmations.extend(loaded_confirmations)

    if not events:
        msg = f"no observable_event records found under {root}"
        raise ValueError(msg)

    return tuple(events), tuple(confirmations)


def write_learning_record(
    project_dir: Path,
    *,
    output_path: Path | None = None,
    validator: ContractValidator | None = None,
    inferencer: CandidateInferencer | None = None,
) -> Path:
    contract_validator = validator if validator is not None else ContractValidator()
    events, confirmations = load_project_records(project_dir, validator=contract_validator)
    bundle = build_archive_bundle(
        events,
        confirmations=confirmations,
        validator=contract_validator,
        inferencer=inferencer,
    )
    markdown = render_markdown(bundle, source_dir=project_dir.resolve())
    destination = (
        output_path
        if output_path is not None
        else project_dir.resolve() / "learning-record.md"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    return destination


def main(argv: list[str] | tuple[str, ...] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.project_dir is None:
        parser.error("the following arguments are required: project_dir")

    project_dir = Path(args.project_dir)
    write_learning_record(project_dir, output_path=args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
