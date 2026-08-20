from __future__ import annotations

import json
from typing import cast

import pytest

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.reporting import render_markdown
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


def test_default_candidate_inferencer_announces_llm_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from learntrace.reporting.llm import LLM_ENABLED_ENV

    monkeypatch.setenv(LLM_API_KEY_ENV, "test-key")
    monkeypatch.setenv(LLM_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.setenv(LLM_ENABLED_ENV, "1")

    default_candidate_inferencer()
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "LLM candidate inference is enabled" in captured.err


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
    assert bundle.inference_mode == "llm"
    assert "候选生成模式：`llm`" in render_markdown(bundle)


def test_llm_payload_omits_source_refs_and_private_fields() -> None:
    """The outbound LLM payload must never contain source_refs or paths."""
    content = json.dumps({"candidates": []}, ensure_ascii=False)
    inferencer = FakeLLMInferencer(content)
    event = ObservableEvent(
        id="evt-secret-1",
        kind=EventKind.TEST_LOG,
        summary="pytest passed.",
        source_refs=(
            SourceRef(
                type=SourceType.FILE,
                ref="C:/Users/student/Documents/project/src/secret_test.py",
                note="含用户名路径",
            ),
        ),
        occurred_at="2026-05-01T10:00:00+08:00",
    )

    inferencer.infer((event,))

    payload = inferencer.payloads[0]
    messages = cast(list[dict[str, object]], payload["messages"])
    content = cast(str, messages[1]["content"])
    sent_fields = json.loads(content.rsplit("ObservableEvent records:\n", 1)[1])[0]
    assert set(sent_fields.keys()) == {"id", "kind", "summary", "occurred_at"}
    assert sent_fields["summary"] == "pytest passed."
    assert sent_fields["id"] == "evt-secret-1"
    assert "source_refs" not in sent_fields


def test_llm_payload_sanitizes_private_markers_in_summary() -> None:
    """A summary that itself embeds an email, path, or token must be scrubbed
    before leaving the boundary, so the payload matches the disclosure that
    personal identifiers / code are not transmitted."""
    content = json.dumps({"candidates": []}, ensure_ascii=False)
    inferencer = FakeLLMInferencer(content)
    event = ObservableEvent(
        id="evt-leak-1",
        kind=EventKind.TEST_LOG,
        summary=(
            "跑了 alice@example.com 的用例，C:/Users/alice/proj/src/x.py 失败；"
            "api_key=sk-1234567890 的命令被跳过。"
        ),
        source_refs=(SourceRef(type=SourceType.FILE, ref="logs/a.log"),),
    )

    inferencer.infer((event,))

    payload = inferencer.payloads[0]
    messages = cast(list[dict[str, object]], payload["messages"])
    content = cast(str, messages[1]["content"])
    sent_fields = json.loads(content.rsplit("ObservableEvent records:\n", 1)[1])[0]

    summary = cast(str, sent_fields["summary"])
    assert "alice@example.com" not in summary
    assert "C:/Users/alice" not in summary
    assert "sk-1234567890" not in summary
    assert "alice@example.com" not in json.dumps(payload, ensure_ascii=False)


def test_llm_payload_sanitizes_bearer_header_and_bare_token() -> None:
    """Bearer auth headers and bare ``ghp_``/``sk-`` tokens embedded in a
    summary must be scrubbed by the shared redaction implementation, not the
    old key=value-only regex which missed both shapes."""
    content = json.dumps({"candidates": []}, ensure_ascii=False)
    inferencer = FakeLLMInferencer(content)
    event = ObservableEvent(
        id="evt-token-1",
        kind=EventKind.TRACE_RECORD,
        summary=(
            "用 git push 时脚本带了 Authorization: Bearer ghp_ABC1234567890 的头，"
            "另外还打印了 sk-abcdefgh12345 这个 token。"
        ),
        source_refs=(SourceRef(type=SourceType.FILE, ref="run.sh"),),
    )

    inferencer.infer((event,))

    payload = inferencer.payloads[0]
    messages = cast(list[dict[str, object]], payload["messages"])
    content = cast(str, messages[1]["content"])
    sent_fields = json.loads(content.rsplit("ObservableEvent records:\n", 1)[1])[0]
    summary = cast(str, sent_fields["summary"])

    assert "ghp_ABC1234567890" not in summary
    assert "sk-abcdefgh12345" not in summary
    assert "ghp_" not in summary
    assert "Authorization: Bearer ghp_" not in summary
    assert "ghp_ABC1234567890" not in json.dumps(payload, ensure_ascii=False)


def test_llm_payload_redacts_all_documented_secret_shapes_and_code() -> None:
    content = json.dumps({"candidates": []}, ensure_ascii=False)
    inferencer = FakeLLMInferencer(content)
    event = ObservableEvent(
        id="evt-outbound-contract-1",
        kind=EventKind.DOCUMENT,
        summary=(
            "tokens: sk-abcdefgh12345678 AKIAABCDEFGHIJKLMNOP "
            "ASIAQRSTUVWXYZABCDEF xoxb-12345678-abcdefgh; "
            "inline `private_call(secret)` and fenced:\n"
            "```python\nprint('repository code')\n```"
        ),
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="notes.md:1-4"),),
    )

    inferencer.infer((event,))

    serialized = json.dumps(inferencer.payloads[0], ensure_ascii=False)
    for forbidden in (
        "sk-abcdefgh12345678",
        "AKIAABCDEFGHIJKLMNOP",
        "ASIAQRSTUVWXYZABCDEF",
        "xoxb-12345678-abcdefgh",
        "private_call(secret)",
        "repository code",
    ):
        assert forbidden not in serialized
    assert serialized.count("[REDACTED]") >= 6


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
