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
