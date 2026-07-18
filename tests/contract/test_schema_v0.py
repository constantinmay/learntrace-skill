"""schema v0 与金标样例的契约回归测试。

由 ``tests/fixtures/golden/expected-results.json`` 驱动：每个样例文件都必须
在清单中登记，并且校验结果（通过或被拒绝）必须与预期完全一致。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, TypedDict

import jsonschema
import pytest

from learntrace.models import (
    SCHEMA_VERSION,
    CandidateStatus,
    ConfirmationDecision,
    ContractValidator,
    EventKind,
    LearningNodeCandidate,
    MissingInfo,
    ObservableEvent,
    RecordType,
    SourceRef,
    SourceType,
    StudentConfirmation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "v0"
GOLDEN_DIR = REPO_ROOT / "tests" / "fixtures" / "golden"
MANIFEST_PATH = GOLDEN_DIR / "expected-results.json"

RECORD_TYPES: tuple[RecordType, ...] = (
    "observable_event",
    "learning_node_candidate",
    "student_confirmation",
)


class ExpectedCase(TypedDict):
    file: str
    record_type: RecordType
    valid: bool
    notes: str


class Manifest(TypedDict):
    description: str
    cases: list[ExpectedCase]


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        data: Any = json.load(handle)
    return data


@pytest.fixture(scope="module")
def validator() -> ContractValidator:
    return ContractValidator(schema_dir=SCHEMA_DIR)


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    manifest: Manifest = _load_json(MANIFEST_PATH)
    return manifest


def _case_id(case: ExpectedCase) -> str:
    return case["file"].replace("/", "__").removesuffix(".json")


def test_schemas_are_valid_json_schema() -> None:
    """每个 Schema 文件自身必须满足 Draft 2020-12 元 Schema。"""
    for schema_path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        jsonschema.Draft202012Validator.check_schema(_load_json(schema_path))


def test_manifest_covers_every_sample(manifest: Manifest) -> None:
    """新增样例文件必须同步登记到预期结果清单，否则此处直接失败。"""
    listed = {case["file"] for case in manifest["cases"]}
    on_disk = {
        str(path.relative_to(GOLDEN_DIR))
        for path in GOLDEN_DIR.rglob("*.json")
        if path.name != MANIFEST_PATH.name
    }
    assert listed == on_disk, f"清单与样例文件不一致: {listed ^ on_disk}"
    for case in manifest["cases"]:
        assert case["record_type"] in RECORD_TYPES


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=_case_id(case)) for case in _load_json(MANIFEST_PATH)["cases"]],
)
def test_golden_samples(validator: ContractValidator, case: ExpectedCase) -> None:
    record = _load_json(GOLDEN_DIR / case["file"])
    errors = validator.iter_errors(case["record_type"], record)
    if case["valid"]:
        assert errors == [], f"预期校验通过，实际错误: {[e.message for e in errors]}"
    else:
        assert errors, f"预期校验拒绝（{case['notes']}），实际却通过"


def test_bad_datetime_rejected_by_format_checker(validator: ContractValidator) -> None:
    """非法日期样例必须因 format 被拒绝，证明 FormatChecker 确实生效。"""
    record = _load_json(GOLDEN_DIR / "invalid" / "event-bad-datetime.json")
    errors = validator.iter_errors("observable_event", record)
    assert any("date-time" in error.message for error in errors)


def test_relative_ref_to_common_defs_resolves(validator: ContractValidator) -> None:
    """违反 common.schema.json 定义的 source_ref 必须通过相对 $ref 被拒绝。"""
    record = _load_json(GOLDEN_DIR / "normal" / "observable-event-commit.json")
    record["source_refs"] = [{"type": "chat_message", "ref": "x"}]
    errors = validator.iter_errors("observable_event", record)
    assert errors, "指向 common source_ref 枚举的相对 $ref 未生效"


def test_models_roundtrip_validates(validator: ContractValidator) -> None:
    """每个模型的 to_dict 输出都必须能通过对应 Schema 校验。"""
    event = ObservableEvent(
        id="evt-model-1",
        kind=EventKind.GIT_COMMIT,
        summary="模型往返测试事件",
        source_refs=(SourceRef(type=SourceType.GIT_COMMIT, ref="abc123"),),
        occurred_at="2026-03-02T14:23:11+08:00",
    )
    candidate = LearningNodeCandidate(
        id="cand-model-1",
        statement="候选陈述",
        basis_event_ids=("evt-model-1",),
        uncertainty="不确定性说明",
        question_to_student="是否如此？",
        status=CandidateStatus.PROPOSED,
    )
    confirmation = StudentConfirmation(
        id="conf-model-1",
        candidate_id="cand-model-1",
        decision=ConfirmationDecision.SUPPLEMENTED,
        student_statement=MissingInfo(note="学生未补充"),
    )
    assert event.to_dict()["schema_version"] == SCHEMA_VERSION
    validator.validate("observable_event", event.to_dict())
    validator.validate("learning_node_candidate", candidate.to_dict())
    validator.validate("student_confirmation", confirmation.to_dict())


def test_missing_info_serializes_to_structured_state() -> None:
    """证据缺失必须序列化为结构化的 missing_info 对象。"""
    data = StudentConfirmation(
        id="conf-model-2",
        candidate_id="cand-model-1",
        decision=ConfirmationDecision.DENIED,
        student_statement=MissingInfo(),
    ).to_dict()
    assert data["student_statement"] == {"status": "not_recorded"}


def _load_bundle(subdir: Literal["normal", "insufficient_evidence"]) -> dict[str, Any]:
    bundle: dict[str, list[Any]] = {"events": [], "candidates": [], "confirmations": []}
    for path in sorted((GOLDEN_DIR / subdir).glob("*.json")):
        record = _load_json(path)
        level = record["evidence_level"]
        if level == "observable_fact":
            bundle["events"].append(record)
        elif level == "candidate_inference":
            bundle["candidates"].append(record)
        elif level == "student_confirmation":
            bundle["confirmations"].append(record)
        else:  # pragma: no cover - 已被上文的 Schema 校验拦截
            raise AssertionError(f"{path} 中出现未知 evidence_level {level!r}")
    return bundle


def _bundle_violations(bundle: dict[str, Any]) -> list[str]:
    """JSON Schema 无法表达的跨记录规则。"""
    violations: list[str] = []
    event_ids = {event["id"] for event in bundle["events"]}
    candidate_ids = {c["id"] for c in bundle["candidates"]}
    answered_ids = {c["candidate_id"] for c in bundle["confirmations"]}
    for candidate in bundle["candidates"]:
        for basis_id in candidate["basis_event_ids"]:
            if basis_id not in event_ids:
                violations.append(f"{candidate['id']}: 依据了不存在的事件 {basis_id}")
        if candidate["status"] == "resolved" and candidate["id"] not in answered_ids:
            violations.append(f"{candidate['id']}: 标记 resolved 但缺少确认记录")
        if candidate["status"] == "proposed" and candidate["id"] in answered_ids:
            violations.append(f"{candidate['id']}: 仍为 proposed 但已有确认记录")
    for confirmation in bundle["confirmations"]:
        if confirmation["candidate_id"] not in candidate_ids:
            violations.append(f"{confirmation['id']}: 回答了不存在的候选")
    return violations


def test_normal_bundle_is_consistent() -> None:
    assert _bundle_violations(_load_bundle("normal")) == []


def test_insufficient_evidence_bundle_is_consistent() -> None:
    bundle = _load_bundle("insufficient_evidence")
    assert _bundle_violations(bundle) == []
    assert all(event["kind"] != "trace_record" for event in bundle["events"])


def test_resolved_candidate_without_confirmation_is_violation() -> None:
    """resolved 绝不能替代独立的学生确认记录。"""
    bundle = _load_bundle("normal")
    bundle["confirmations"] = []
    assert any("缺少确认记录" in v for v in _bundle_violations(bundle))
