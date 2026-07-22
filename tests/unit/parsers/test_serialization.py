from __future__ import annotations

import json
from pathlib import Path

import pytest

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
