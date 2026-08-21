from __future__ import annotations

import json
from typing import cast

import pytest

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.reporting import render_markdown, render_questions_markdown
from learntrace.reporting.llm import (
    DEFAULT_LLM_MAX_TOKENS,
    LLM_API_KEY_ENV,
    LLM_BASE_URL_ENV,
    LLM_MAX_TOKENS_ENV,
    LLM_MODEL_ENV,
    LLMConfig,
    LLMInferenceError,
    OpenAIChatCandidateInferencer,
    default_candidate_inferencer,
    llm_config_from_env,
)
from learntrace.reporting.pipeline import (
    StubCandidateInferencer,
    build_archive_bundle,
    bundle_to_dict,
)


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


def test_llm_config_from_env_accepts_bounded_max_tokens() -> None:
    config = llm_config_from_env({LLM_API_KEY_ENV: "secret", LLM_MAX_TOKENS_ENV: "12000"})

    assert config is not None
    assert config.max_tokens == 12000


@pytest.mark.parametrize("value", ["not-a-number", "1", "999999"])
def test_llm_config_from_env_rejects_invalid_max_tokens(value: str) -> None:
    with pytest.raises(ValueError, match=LLM_MAX_TOKENS_ENV):
        llm_config_from_env({LLM_API_KEY_ENV: "secret", LLM_MAX_TOKENS_ENV: value})


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
    assert inferencer.payloads[0]["max_tokens"] == DEFAULT_LLM_MAX_TOKENS
    assert bundle.inference_mode == "llm"
    assert "候选生成模式：`llm`" in render_markdown(bundle)


def test_llm_empty_candidate_result_falls_back_to_stub() -> None:
    inferencer = FakeLLMInferencer(json.dumps({"candidates": []}))

    bundle = build_archive_bundle(
        (
            _event(
                "evt-demo-trace",
                EventKind.TRACE_RECORD,
                "AI suggested regex validation for student identifiers.",
            ),
            _event(
                "evt-demo-commit",
                EventKind.GIT_COMMIT,
                "Commit a1b2c3d: replace regex validation with a parser helper.",
            ),
        ),
        inferencer=inferencer,
    )

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "revise_ai_suggestion"
    assert bundle.inference_mode == "llm_stub_fallback"
    assert [warning.code for warning in bundle.warnings] == [
        "llm_no_candidates",
        "llm_fallback_to_stub",
    ]


def test_llm_length_response_reports_actionable_limit_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "", "reasoning_content": "thinking"},
                        }
                    ],
                    "usage": {"completion_tokens_details": {"reasoning_tokens": 8000}},
                }
            ).encode()

    def fake_urlopen(*args: object, **kwargs: object) -> Response:
        del args, kwargs
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    inferencer = OpenAIChatCandidateInferencer(
        LLMConfig(api_key="test-key", base_url="https://example.test/v1")
    )

    bundle = build_archive_bundle(
        (_event("evt-document", EventKind.DOCUMENT, "项目目标：完成课程项目。"),),
        inferencer=inferencer,
    )

    assert bundle.inference_mode == "llm_stub_fallback"
    assert [warning.code for warning in bundle.warnings] == [
        "llm_output_limit_reached",
        "llm_fallback_to_stub",
    ]
    assert LLM_MAX_TOKENS_ENV in bundle.warnings[0].message
    assert "reasoning_tokens=8000" in bundle.warnings[0].message


def test_zero_candidate_bundle_keeps_reflection_questions_without_inventing_candidate() -> None:
    bundle = build_archive_bundle(
        (_event("evt-document", EventKind.DOCUMENT, "项目说明记录了实现范围。"),),
        inferencer=StubCandidateInferencer(),
    )

    archive = bundle_to_dict(bundle)
    pending = cast(list[dict[str, object]], archive["pending_questions"])
    markdown = render_questions_markdown(bundle)

    assert bundle.candidates == ()
    assert pending
    assert all(question["candidate_id"] is None for question in pending)
    assert "gap-test-evidence" in markdown
    assert "gap-learning-reflection" in markdown


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
            "根路径 /secret 无法读取；api_key=sk-1234567890 的命令被跳过。"
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
    assert "/secret" not in summary
    assert "sk-1234567890" not in summary
    assert "alice@example.com" not in json.dumps(payload, ensure_ascii=False)


def test_llm_payload_preserves_routes_urls_and_natural_slashes() -> None:
    content = json.dumps({"candidates": []}, ensure_ascii=False)
    inferencer = FakeLLMInferencer(content)
    event = ObservableEvent(
        id="evt-route-1",
        kind=EventKind.DOCUMENT,
        summary=(
            "读书/资源管理系统通过 /api 和 /api/books 提供接口，"
            "文档位于 https://example.test/docs；根路径 /secret 不应发送。"
        ),
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="README.md:1-2"),),
    )

    inferencer.infer((event,))

    payload = inferencer.payloads[0]
    messages = cast(list[dict[str, object]], payload["messages"])
    sent_fields = json.loads(
        cast(str, messages[1]["content"]).rsplit("ObservableEvent records:\n", 1)[1]
    )[0]
    summary = cast(str, sent_fields["summary"])

    assert "读书/资源管理系统" in summary
    assert "/api 和" in summary
    assert "/api/books" in summary
    assert "https://example.test/docs" in summary
    assert "/secret" not in summary


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
    assert "[REDACTED]" in serialized


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


def test_archive_falls_back_when_llm_response_is_unparseable() -> None:
    inferencer = FakeLLMInferencer('```json\n{"candidates": [}\n```')
    events = (
        _event(
            "evt-demo-commit",
            EventKind.GIT_COMMIT,
            "Commit a1b2c3d: fix parser handling for quoted values.",
        ),
    )

    bundle = build_archive_bundle(events, inferencer=inferencer)

    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].node_type.value == "fix_failed_approach"
    assert bundle.inference_mode == "llm_stub_fallback"
    assert [warning.code for warning in bundle.warnings] == [
        "llm_inference_failed",
        "llm_fallback_to_stub",
    ]


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
    assert [warning.code for warning in bundle.warnings] == [
        "invalid_llm_candidates_discarded",
        "llm_no_candidates",
        "llm_fallback_to_stub",
    ]
    assert bundle.inference_mode == "llm_stub_fallback"
