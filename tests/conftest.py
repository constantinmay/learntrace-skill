from __future__ import annotations

import pytest

from learntrace.reporting.llm import LLM_API_KEY_ENV, LLM_BASE_URL_ENV, LLM_MODEL_ENV


@pytest.fixture(autouse=True)
def clear_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (LLM_API_KEY_ENV, LLM_BASE_URL_ENV, LLM_MODEL_ENV):
        monkeypatch.delenv(key, raising=False)
