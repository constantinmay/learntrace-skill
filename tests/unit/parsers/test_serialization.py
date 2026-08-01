from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers import ParseResult, write_parse_result
from learntrace.parsers import serialization as serialization_module


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


def test_does_not_follow_predictable_temporary_symlink(tmp_path: Path) -> None:
    output = tmp_path / "parsed.json"
    outside = tmp_path / "must-not-change.txt"
    outside.write_text("original\n", encoding="utf-8")
    predictable = tmp_path / ".parsed.json.tmp"
    try:
        predictable.symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this platform")

    write_parse_result(ParseResult(events=(_event(),)), output)

    assert outside.read_text(encoding="utf-8") == "original\n"
    assert json.loads(output.read_text(encoding="utf-8"))["events"][0]["id"] == _event().id


def test_concurrent_writes_always_leave_one_complete_result(tmp_path: Path) -> None:
    output = tmp_path / "parsed.json"
    first = ParseResult(events=(_event(),))
    second_event = replace(
        _event(),
        id="evt-document-concurrent",
        summary="文档记录：并发内容。",
    )
    second = ParseResult(events=(second_event,))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(write_parse_result, result, output) for result in (first, second)
        ]
        for future in futures:
            future.result()

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["events"][0]["id"] in {_event().id, second_event.id}
    assert list(tmp_path.glob(".parsed.json.*.tmp")) == []


def test_removes_random_temporary_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "parsed.json"

    def reject_replace(source: object, target: object) -> None:
        del source, target
        raise OSError("replace failed")

    monkeypatch.setattr(serialization_module.os, "replace", reject_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_parse_result(ParseResult(events=(_event(),)), output)

    assert not output.exists()
    assert list(tmp_path.glob(".parsed.json.*.tmp")) == []
