from __future__ import annotations

import json

import pytest

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.reporting.llm import (
    LLM_API_KEY_ENV,
    LLM_BASE_URL_ENV,
    LLM_MODEL_ENV,
    LLMConfig,
    LLMInferenceError,
    OpenAIChatCandidateInferencer,
    default_candidate_inferencer,
    llm_config_from_env,
)
from learntrace.reporting.pipeline import StubCandidateInferencer, build_archive_bundle


def _event(record_id: str, kind: EventKind, summary: str) -> ObservableEvent:
    return ObservableEvent(
        id=record_id,
        kind=kind,
        summary=summary,
        source_refs=(SourceRef(type=SourceType.FILE, ref=f"{record_id}.txt"),),
    )


class FakeLLMInferencer(OpenAIChatCandidateInferencer):
    def __init__(self, content: str) -> None:
        super().__init__(LLMConfig(api_key="test-key", base_url="https://example.test/v1"))
        self.payloads: list[dict[str, object]] = []
        self._content = content

    def _complete(self, payload: dict[str, object]) -> str:
        self.payloads.append(payload)
        return self._content


def test_llm_config_from_env_uses_defaults_and_hides_key_repr() -> None:
    config = llm_config_from_env({LLM_API_KEY_ENV: "secret"})

    assert config is not None
    assert config.base_url == "https://api.llm.ustc.edu.cn/v1"
    assert config.model == "smart/default"
    assert "secret" not in repr(config)


def test_llm_config_from_env_accepts_base_url_and_model() -> None:
    config = llm_config_from_env(
        {
            LLM_API_KEY_ENV: "secret",
            LLM_BASE_URL_ENV: "https://example.test/v1/",
            LLM_MODEL_ENV: "smart/default",
        }
    )

    assert config is not None
    assert config.base_url == "https://example.test/v1"
    assert config.model == "smart/default"


def test_default_candidate_inferencer_uses_stub_without_key() -> None:
    assert isinstance(default_candidate_inferencer(), StubCandidateInferencer)


def test_default_candidate_inferencer_uses_llm_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from learntrace.reporting.llm import LLM_ENABLED_ENV

    monkeypatch.setenv(LLM_API_KEY_ENV, "test-key")
    monkeypatch.setenv(LLM_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.setenv(LLM_ENABLED_ENV, "1")

    assert isinstance(default_candidate_inferencer(), OpenAIChatCandidateInferencer)


def test_default_candidate_inferencer_stub_despite_key_without_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from learntrace.reporting.llm import LLM_ENABLED_ENV

    monkeypatch.setenv(LLM_API_KEY_ENV, "test-key")
    monkeypatch.setenv(LLM_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.delenv(LLM_ENABLED_ENV, raising=False)

    assert isinstance(default_candidate_inferencer(), StubCandidateInferencer)


def test_llm_inferencer_returns_schema_valid_candidate() -> None:
    content = json.dumps(
        {
            "candidates": [
                {
                    "node_type": "revise_ai_suggestion",
                    "statement": "学生可能比较了 AI 的数据校验建议，并改用更适合项目约束的实现。",
                    "basis_event_ids": ["evt-demo-trace", "evt-demo-commit"],
                    "uncertainty": "中：AI 建议和提交内容相邻，但仍需学生确认修改动机。",
                    "question_to_student": "这次提交是否是在比较 AI 建议后主动改写实现方案？",
                }
            ]
        },
        ensure_ascii=False,
    )
    inferencer = FakeLLMInferencer(content)
    events = (
        _event("evt-demo-trace", EventKind.TRACE_RECORD, "AI suggested regex validation."),
        _event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit switched to parser helper."),
    )

    bundle = build_archive_bundle(events, inferencer=inferencer)

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "revise_ai_suggestion"
    assert bundle.candidates[0].basis_event_ids == ("evt-demo-trace", "evt-demo-commit")
    assert inferencer.payloads
    assert inferencer.payloads[0]["max_tokens"] == 3000


def test_llm_inferencer_accepts_fenced_json() -> None:
    payload = {
        "candidates": [
            {
                "node_type": "add_tests",
                "statement": (
                    "Student may have added edge-case tests after seeing the parser boundary."
                ),
                "basis_event_ids": ["evt-demo-commit", "evt-demo-test"],
                "uncertainty": (
                    "\u4e2d\uff1aThe commit and test log are related, "
                    "but intent still needs student confirmation."
                ),
                "question_to_student": (
                    "Were these edge-case tests added after you identified the parser boundary?"
                ),
            }
        ]
    }
    content = f"```json\n{json.dumps(payload)}\n```"
    inferencer = FakeLLMInferencer(content)

    bundle = build_archive_bundle(
        (
            _event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit added parser tests."),
            _event("evt-demo-test", EventKind.TEST_LOG, "pytest passed edge cases."),
        ),
        inferencer=inferencer,
    )

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "add_tests"


def test_llm_inferencer_wraps_unparseable_json() -> None:
    inferencer = FakeLLMInferencer('```json\n{"candidates": [}\n```')

    with pytest.raises(LLMInferenceError, match="could not be parsed"):
        inferencer.infer(
            (_event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit added parser tests."),)
        )


def test_llm_inferencer_ai_candidate_requires_trace_record() -> None:
    """AI-type candidates (revise_ai_suggestion, follow_up) require at least
    one trace_record in basis_event_ids."""
    content = json.dumps(
        {
            "candidates": [
                {
                    "node_type": "revise_ai_suggestion",
                    "statement": "学生可能修改了 AI 建议。",
                    "basis_event_ids": ["evt-demo-commit"],
                    "uncertainty": "中：内容相关。",
                    "question_to_student": "请确认。",
                }
            ]
        },
        ensure_ascii=False,
    )
    inferencer = FakeLLMInferencer(content)
    events = (_event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit message."),)
    bundle = build_archive_bundle(events, inferencer=inferencer)
    # Candidate should be dropped because no trace_record exists
    assert bundle.candidates == ()


def test_llm_inferencer_ai_candidate_accepted_with_trace_record() -> None:
    """AI-type candidates are accepted when at least one trace_record event exists."""
    content = json.dumps(
        {
            "candidates": [
                {
                    "node_type": "revise_ai_suggestion",
                    "statement": "学生可能参考并修改了 AI 建议。",
                    "basis_event_ids": ["evt-demo-trace", "evt-demo-commit"],
                    "uncertainty": "中：AI 建议与提交相邻。",
                    "question_to_student": "请确认是否修改了 AI 建议。",
                }
            ]
        },
        ensure_ascii=False,
    )
    inferencer = FakeLLMInferencer(content)
    events = (
        _event("evt-demo-trace", EventKind.TRACE_RECORD, "AI suggestion."),
        _event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit message."),
    )
    bundle = build_archive_bundle(events, inferencer=inferencer)
    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "revise_ai_suggestion"


def test_llm_inferencer_drops_candidates_with_unknown_basis_event() -> None:
    content = json.dumps(
        {
            "candidates": [
                {
                    "node_type": "add_tests",
                    "statement": "学生可能补充了测试。",
                    "basis_event_ids": ["evt-missing"],
                    "uncertainty": "高：候选引用了不存在的事实。",
                    "question_to_student": "这条候选是否有效？",
                }
            ]
        },
        ensure_ascii=False,
    )
    inferencer = FakeLLMInferencer(content)

    bundle = build_archive_bundle(
        (_event("evt-demo-commit", EventKind.GIT_COMMIT, "Commit added tests."),),
        inferencer=inferencer,
    )

    assert bundle.candidates == ()
