from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers import ParseResult, write_parse_result


def _event() -> ObservableEvent:
    return ObservableEvent(
        id="evt-document-serialization",
        kind=EventKind.DOCUMENT,
        summary="文档记录：中文内容。",
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="README.md:1-1"),),
    )


def test_writes_validated_utf8_batch_json(tmp_path: Path) -> None:
    output = tmp_path / "output" / "parsed.json"

    write_parse_result(ParseResult(events=(_event(),)), output)

    text = output.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "中文内容" in text
    assert data["events"][0]["id"] == "evt-document-serialization"
    assert data["parser_version"] == "v0"


def test_rejects_duplicate_event_ids_before_writing(tmp_path: Path) -> None:
    event = _event()

    with pytest.raises(ValueError, match="duplicate observable event ids"):
        write_parse_result(ParseResult(events=(event, event)), tmp_path / "parsed.json")

    assert not (tmp_path / "parsed.json").exists()


def test_rejects_schema_invalid_event_before_writing(tmp_path: Path) -> None:
    invalid = ObservableEvent(
        id="evt-document-without-source",
        kind=EventKind.DOCUMENT,
        summary="缺少来源引用的无效事实。",
        source_refs=(),
    )
    output = tmp_path / "parsed.json"

    with pytest.raises(ValidationError, match="source_refs"):
        write_parse_result(ParseResult(events=(invalid,)), output)

    assert not output.exists()
