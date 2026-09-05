"""Persistent model settings with secrets delegated to the OS credential store."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring
from keyring.errors import KeyringError

_SERVICE = "LearnTrace"
_ACCOUNT = "model-api-key"
_PROTOCOLS = {"anthropic-messages", "openai-completions", "openai-responses"}
_THINKING = {"off", "minimal", "low", "medium", "high"}


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model_id: str
    api_protocol: str = "anthropic-messages"
    thinking_level: str = "medium"

    def validate(self) -> None:
        scheme, host = self._url_parts(self.base_url)
        local_http = scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}
        if scheme != "https" and not local_http:
            raise ValueError("API 地址必须使用 HTTPS；只有本机服务可以使用 HTTP。")
        if not self.model_id.strip():
            raise ValueError("模型 ID 不能为空。")
        if self.api_protocol not in _PROTOCOLS:
            raise ValueError("不支持的 API 协议。")
        if self.thinking_level not in _THINKING:
            raise ValueError("不支持的思考强度。")

    @staticmethod
    def _url_parts(raw: str) -> tuple[str, str]:
        from urllib.parse import urlsplit

        parts = urlsplit(raw.strip())
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
        ):
            raise ValueError("API 地址必须使用 HTTPS；只有本机服务可以使用 HTTP。")
        return parts.scheme, parts.hostname


class ModelConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ModelConfig | None:
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            config = ModelConfig(**raw)
            config.validate()
            return config
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def save(self, config: ModelConfig, api_key: str | None) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(asdict(config), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        if api_key:
            try:
                keyring.set_password(_SERVICE, _ACCOUNT, api_key)
            except KeyringError as error:
                raise RuntimeError(f"系统凭据库无法保存 API Key：{error}") from error

    def api_key(self) -> str | None:
        try:
            return keyring.get_password(_SERVICE, _ACCOUNT)
        except KeyringError as error:
            raise RuntimeError(f"系统凭据库无法读取 API Key：{error}") from error

    def public(
        self,
        *,
        has_api_key: bool | None = None,
        credential_error: str | None = None,
    ) -> dict[str, object]:
        config = self.load()
        result: dict[str, object] = {
            "base_url": config.base_url if config else "",
            "model_id": config.model_id if config else "",
            "api_protocol": config.api_protocol if config else "",
            "thinking_level": config.thinking_level if config else "medium",
            "has_api_key": bool(self.api_key()) if has_api_key is None else has_api_key,
        }
        if credential_error:
            result["credential_error"] = credential_error
        return result
