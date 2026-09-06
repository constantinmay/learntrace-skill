from __future__ import annotations

import json
from pathlib import Path

import pytest

from learntrace.ui.model_config import ModelConfig, ModelConfigStore


def test_model_config_persists_metadata_but_not_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets: dict[tuple[str, str], str] = {}

    def set_password(service: str, account: str, value: str) -> None:
        secrets[service, account] = value

    def get_password(service: str, account: str) -> str | None:
        return secrets.get((service, account))

    monkeypatch.setattr(
        "learntrace.ui.model_config.keyring.set_password",
        set_password,
    )
    monkeypatch.setattr(
        "learntrace.ui.model_config.keyring.get_password",
        get_password,
    )
    path = tmp_path / "model-config.json"
    store = ModelConfigStore(path)
    store.save(
        ModelConfig("https://api.example.com", "glm-5.2-107", "anthropic-messages", "high"),
        "secret-value",
    )

    assert store.api_key() == "secret-value"
    assert store.public()["has_api_key"] is True
    assert "secret-value" not in path.read_text(encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["model_id"] == "glm-5.2-107"


def test_model_config_rejects_insecure_remote_http(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        ModelConfigStore(tmp_path / "config.json").save(
            ModelConfig("http://api.example.com", "model"), "secret"
        )


ALLOWED_BASE_URLS = (
    "https://api.example.com",
    "https://[::1]:11434",
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    "http://[::1]:11434",
)

REJECTED_BASE_URLS = (
    "http://api.example.com",
    "http://127.0.0.1.evil.com",
    "http://127.0.0.1@evil.com",
    "http://localhost.attacker.test",
    "https://user:pass@api.example.com",
    "ws://example.com",
)


def test_model_config_url_validation_uses_exact_loopback_hosts() -> None:
    for url in ALLOWED_BASE_URLS:
        ModelConfig(url, "model").validate()

    for url in REJECTED_BASE_URLS:
        with pytest.raises(ValueError, match="HTTPS"):
            ModelConfig(url, "model").validate()
