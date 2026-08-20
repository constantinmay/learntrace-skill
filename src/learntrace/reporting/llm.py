"""OpenAI-compatible LLM candidate inference for LearnTrace Task 4."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from learntrace.models import (
    CandidateStatus,
    ContractValidator,
    LearningNodeCandidate,
    NodeType,
    ObservableEvent,
)
from learntrace.privacy import redact_sensitive_text
from learntrace.reporting.pipeline import (
    ArchiveWarning,
    CandidateDraft,
    CandidateInferencer,
    StubCandidateInferencer,
    stable_candidate_id,
)

LLM_API_KEY_ENV = "LEARNTRACE_LLM_API_KEY"
LLM_BASE_URL_ENV = "LEARNTRACE_LLM_BASE_URL"
LLM_MODEL_ENV = "LEARNTRACE_LLM_MODEL"
LLM_ENABLED_ENV = "LEARNTRACE_LLM_ENABLED"
DEFAULT_LLM_BASE_URL = "https://api.llm.ustc.edu.cn/v1"
DEFAULT_LLM_MODEL = "smart/default"
_UNCERTAINTY_PREFIXES = ("\u9ad8\uff1a", "\u4e2d\uff1a", "\u4f4e\uff1a")

# Outbound-boundary redaction: even though `source_refs` (with filesystem
# paths) and notes are never transmitted, a summary may itself embed an email,
# a token, or an absolute path. These are scrubbed here so the LLM payload
# matches the privacy disclosure: repository code and personal identifiers are
# not sent out.
#
# Secret shapes (Bearer headers, ghp_/sk-/xox tokens, key=value assignments,
# private-key blocks) are delegated to `learntrace.privacy.redact_sensitive_text`
# so the outbound boundary reuses one shared, tested redaction implementation
# instead of a second, weaker regex.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DISK_PATH_RE = re.compile(
    r"((?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\uff0c\u3002\uff1b\u3001\"'`]+|"
    r"\\\\[^\\/\s]+[\\/][^\s\uff0c\u3002\uff1b\u3001\"'`]+|"
    r"(?<![\w:/])/(?!api(?:/|\b)|v\d+(?:/|\b))"
    r"[^\s\uff0c\u3002\uff1b\u3001\"'`]+)"
)
_WINDOWS_HOME_RE = re.compile(
    r"[Cc]:[\\/][Uu]sers[\\/][^\\/]+(?:[\\/][^\s\uff0c\u3002\uff1b\u3001\"'`]*)?"
)
_FENCED_CODE_RE = re.compile(r"(?:`{3,}|~{3,})[\s\S]*?(?:(?:`{3,}|~{3,})|$)")
_INLINE_CODE_RE = re.compile(r"`[^`\r\n]+`")


def _sanitize_summary(summary: str) -> str:
    """Redact personal identifiers and filesystem hints from a summary.

    Preserves the semantic gist for candidate inference while removing secrets
    (Bearer headers, bare tokens, key=value assignments), emails, and absolute
    (incl. Windows user) paths that could tie an archive back to an individual.

    Secret scrubbing reuses `learntrace.privacy.redact_sensitive_text`; a large
    ``limit`` is passed so a long summary's semantics are not truncated here.
    """
    text = _FENCED_CODE_RE.sub("[REDACTED]", summary)
    text = _INLINE_CODE_RE.sub("[REDACTED]", text)
    text = redact_sensitive_text(text, limit=len(text) or 1)
    text = _EMAIL_RE.sub("<email>", text)
    text = _WINDOWS_HOME_RE.sub("<user-path>", text)
    text = _DISK_PATH_RE.sub("<path>", text)
    return text


@dataclass(frozen=True, slots=True)
class LLMConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = DEFAULT_LLM_MODEL
    timeout_seconds: float = 180.0
    max_tokens: int = 3000


class LLMInferenceError(RuntimeError):
    """Raised when a configured LLM call cannot return parseable candidates."""


class OpenAIChatCandidateInferencer:
    """LLM-backed inferencer for OpenAI-compatible chat completion endpoints."""

    inference_mode = "llm"

    def __init__(
        self,
        config: LLMConfig,
        *,
        validator: ContractValidator | None = None,
    ) -> None:
        self._config = config
        self._validator = validator if validator is not None else ContractValidator()
        self._inference_warnings: tuple[ArchiveWarning, ...] = ()

    def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        self._inference_warnings = ()
        if not events:
            return ()
        payload = self._build_payload(events)
        content = self._complete(payload)
        raw_candidates = _extract_llm_candidates(content)
        drafts = self._validated_drafts(raw_candidates, events)
        warnings: list[ArchiveWarning] = []
        rejected_count = len(raw_candidates) - len(drafts)
        if rejected_count:
            warnings.append(
                ArchiveWarning(
                    code="invalid_llm_candidates_discarded",
                    source="candidate_inference",
                    message=(f"LLM 返回的候选中有 {rejected_count} 条因校验失败或重复未进入档案。"),
                )
            )
        if not drafts:
            warnings.append(
                ArchiveWarning(
                    code="llm_no_candidates",
                    source="candidate_inference",
                    message="LLM 未生成可用候选；归档流水线将尝试本地确定性回退。",
                )
            )
        self._inference_warnings = tuple(warnings)
        return drafts

    def inference_warnings(self) -> tuple[ArchiveWarning, ...]:
        """Return diagnostics from the most recent inference call."""

        return self._inference_warnings

    @staticmethod
    def _redacted_event(event: ObservableEvent) -> dict[str, str | None]:
        """Project an event to only the fields the LLM needs to infer with.

        Deliberately omits ``source_refs`` (which may embed filesystem paths or
        notes) and any student-identifying metadata: the model receives only the
        event kind, a sanitized summary, and an optional timestamp. The summary
        is scrubbed at the outbound boundary (emails, tokens, paths) so the
        payload matches the privacy disclosure.
        """
        return {
            "id": event.id,
            "kind": str(event.kind),
            "summary": _sanitize_summary(event.summary),
            "occurred_at": event.occurred_at,
        }

    def _build_payload(self, events: tuple[ObservableEvent, ...]) -> dict[str, object]:
        node_types = ", ".join(item.value for item in NodeType)
        event_json = json.dumps(
            [self._redacted_event(event) for event in sorted(events, key=_event_sort_key)],
            ensure_ascii=False,
            indent=2,
        )
        system_prompt = (
            "You are LearnTrace Task 4. Infer candidate learning moments from "
            "observable events without turning inference into fact. Return strict JSON only."
        )
        user_prompt = (
            "Given these ObservableEvent records, return JSON with this shape:\n"
            '{"candidates":[{"node_type":"follow_up","statement":"...",'
            '"basis_event_ids":["evt-id"],"uncertainty":"\u4e2d\uff1areason",'
            '"question_to_student":"..."}]}\n'
            f"Allowed node_type values: {node_types}.\n"
            "Rules:\n"
            "- Use only event ids present in the input for basis_event_ids.\n"
            "- uncertainty must start with exactly \u9ad8\uff1a, \u4e2d\uff1a, or \u4f4e\uff1a.\n"
            "- Keep statements as candidate inference, for example: \u5b66\u751f\u53ef\u80fd...\n"
            "- Ask a concrete question_to_student for confirmation.\n"
            '- If evidence is insufficient, return {"candidates":[]}.\n\n'
            f"ObservableEvent records:\n{event_json}"
        )
        return {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self._config.max_tokens,
        }

    def _complete(self, payload: dict[str, object]) -> str:
        endpoint = f"{self._config.base_url}/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._config.timeout_seconds,
            ) as response:
                raw_response = response.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "LLM response was not valid UTF-8"
            raise LLMInferenceError(msg) from exc
        except (OSError, urllib.error.URLError) as exc:
            msg = f"LLM request failed: {exc}"
            raise LLMInferenceError(msg) from exc

        try:
            data = cast(dict[str, Any], json.loads(raw_response))
            choices = data["choices"]
            first_choice = choices[0]
            message = first_choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            msg = "LLM response did not contain choices[0].message.content"
            raise LLMInferenceError(msg) from exc
        if not isinstance(content, str) or not content.strip():
            msg = "LLM response content was empty"
            raise LLMInferenceError(msg)
        return content

    def _validated_drafts(
        self,
        raw_candidates: list[dict[str, object]],
        events: tuple[ObservableEvent, ...],
    ) -> tuple[CandidateDraft, ...]:
        event_ids = {event.id for event in events}
        event_by_id = {event.id: event for event in events}
        drafts: list[CandidateDraft] = []
        used_ids: set[str] = set()
        for raw in raw_candidates:
            draft = _candidate_draft_from_llm(raw, event_ids, event_by_id)
            if draft is None:
                continue
            candidate_id = stable_candidate_id(draft)
            if candidate_id in used_ids:
                continue
            used_ids.add(candidate_id)
            candidate = LearningNodeCandidate(
                id=candidate_id,
                node_type=draft.node_type,
                statement=draft.statement,
                basis_event_ids=draft.basis_event_ids,
                uncertainty=draft.uncertainty,
                question_to_student=draft.question_to_student,
                status=CandidateStatus.PROPOSED,
            )
            if self._validator.is_valid("learning_node_candidate", candidate.to_dict()):
                drafts.append(draft)
        return tuple(drafts)


def llm_config_from_env(env: Mapping[str, str] | None = None) -> LLMConfig | None:
    values = env if env is not None else os.environ
    api_key = values.get(LLM_API_KEY_ENV)
    if api_key is None or not api_key.strip():
        return None
    base_url = values.get(LLM_BASE_URL_ENV) or DEFAULT_LLM_BASE_URL
    model = values.get(LLM_MODEL_ENV) or DEFAULT_LLM_MODEL
    return LLMConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
    )


def default_candidate_inferencer() -> CandidateInferencer:
    config = llm_config_from_env()
    if config is None:
        return StubCandidateInferencer()

    enabled = os.environ.get(LLM_ENABLED_ENV, "").strip().lower()
    if enabled not in ("1", "true", "yes"):
        return StubCandidateInferencer()

    print(
        "INFO: LLM candidate inference is enabled. "
        "For each event the following fields are sent to the configured LLM "
        "endpoint: id, kind, a sanitized summary, occurred_at. "
        "Each summary is scrubbed at the outbound boundary; source_refs (which "
        "may contain paths), notes, and repository code are NOT transmitted.",
        file=sys.stderr,
    )
    return OpenAIChatCandidateInferencer(config)


def _event_sort_key(event: ObservableEvent) -> tuple[str, str]:
    return (event.occurred_at or "", event.id)


def _extract_llm_candidates(content: str) -> list[dict[str, object]]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        msg = "LLM response did not contain a JSON object"
        raise LLMInferenceError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = "LLM JSON response could not be parsed"
        raise LLMInferenceError(msg) from exc
    if not isinstance(data, dict):
        msg = "LLM JSON response must be an object"
        raise LLMInferenceError(msg)
    response = cast(dict[str, object], data)
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        msg = "LLM JSON response must contain a candidates list"
        raise LLMInferenceError(msg)
    raw_candidates = cast(list[object], candidates)
    return [
        cast(dict[str, object], candidate)
        for candidate in raw_candidates
        if isinstance(candidate, dict)
    ]


_AI_NODE_TYPES_REQUIRING_TRACE: frozenset[str] = frozenset({"revise_ai_suggestion", "follow_up"})


def _candidate_draft_from_llm(
    raw: dict[str, object],
    event_ids: set[str],
    event_by_id: dict[str, ObservableEvent],
) -> CandidateDraft | None:
    node_type_raw = raw.get("node_type")
    statement = raw.get("statement")
    basis_event_ids_raw = raw.get("basis_event_ids")
    uncertainty = raw.get("uncertainty")
    question_to_student = raw.get("question_to_student")
    if not (
        isinstance(node_type_raw, str)
        and isinstance(statement, str)
        and isinstance(basis_event_ids_raw, list)
        and isinstance(uncertainty, str)
        and isinstance(question_to_student, str)
    ):
        return None
    try:
        node_type = NodeType(node_type_raw)
    except ValueError:
        return None
    basis_event_ids = tuple(
        item for item in cast(list[object], basis_event_ids_raw) if isinstance(item, str)
    )
    if not basis_event_ids or any(item not in event_ids for item in basis_event_ids):
        return None
    # AI-type (revise_ai_suggestion / follow_up) candidates require at least
    # one trace_record basis event to prevent hallucinated associations.
    if node_type.value in _AI_NODE_TYPES_REQUIRING_TRACE:
        has_trace = any(
            event_by_id.get(eid) is not None and event_by_id[eid].kind.value == "trace_record"
            for eid in basis_event_ids
        )
        if not has_trace:
            return None
    if not uncertainty.startswith(_UNCERTAINTY_PREFIXES):
        return None
    if not statement.strip() or not question_to_student.strip():
        return None
    return CandidateDraft(
        node_type=node_type,
        statement=statement.strip(),
        basis_event_ids=basis_event_ids,
        uncertainty=uncertainty.strip(),
        question_to_student=question_to_student.strip(),
    )
