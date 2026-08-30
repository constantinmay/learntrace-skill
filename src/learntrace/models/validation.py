"""LearnTrace v0 记录的 JSON Schema 校验。

使用 ``referencing.Registry``，使各记录 Schema 与 ``common.schema.json``
之间的相对 ``$ref`` 能够解析；并始终启用 ``FormatChecker``，
确保 ``date-time`` 字段真正被校验。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

RecordType = Literal[
    "observable_event", "learning_node_candidate", "student_confirmation", "narrative_payload"
]

SCHEMA_FILES: dict[RecordType, str] = {
    "observable_event": "observable-event.schema.json",
    "learning_node_candidate": "learning-node-candidate.schema.json",
    "student_confirmation": "student-confirmation.schema.json",
    "narrative_payload": "narrative-payload.schema.json",
}

_COMMON_SCHEMA_FILE = "common.schema.json"

SchemaDict = dict[str, Any]


class _Validator(Protocol):
    """此处用到的 ``jsonschema`` 校验器接口子集。

    ``Draft202012Validator`` 是动态构建的类，其方法类型带有 ``Unknown``；
    通过此 Protocol 保持 strict 模式下的类型完整。
    """

    def validate(self, instance: Any) -> None: ...

    def iter_errors(self, instance: Any) -> Iterator[jsonschema.ValidationError]: ...

    def is_valid(self, instance: Any) -> bool: ...


def default_schema_dir() -> Traversable:
    """返回随 Python 包发布的 v0 Schema 资源目录。"""
    schema_dir = files("learntrace").joinpath("schemas", "v0")
    if schema_dir.is_dir():
        return schema_dir
    msg = "Python 包中缺少 schemas/v0 资源目录"
    raise FileNotFoundError(msg)


def _load_schema(schema_dir: Path | Traversable, filename: str) -> SchemaDict:
    with schema_dir.joinpath(filename).open(encoding="utf-8") as handle:
        schema: SchemaDict = json.load(handle)
    return schema


class ContractValidator:
    """依据 v0 Schema 校验记录 dict。"""

    def __init__(self, schema_dir: Path | Traversable | None = None) -> None:
        self._schema_dir = schema_dir if schema_dir is not None else default_schema_dir()
        schemas: dict[str, SchemaDict] = {
            filename: _load_schema(self._schema_dir, filename)
            for filename in [*SCHEMA_FILES.values(), _COMMON_SCHEMA_FILE]
        }
        pairs: list[tuple[str, Resource[Any]]] = [
            (filename, Resource.from_contents(schema, default_specification=DRAFT202012))
            for filename, schema in schemas.items()
        ]
        registry: Registry[Any] = Registry[Any]().with_resources(pairs)
        self._validators: dict[RecordType, _Validator] = {
            record_type: cast(
                "_Validator",
                jsonschema.Draft202012Validator(
                    schemas[filename],
                    registry=registry,
                    format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
                ),
            )
            for record_type, filename in SCHEMA_FILES.items()
        }

    @property
    def schema_dir(self) -> Path | Traversable:
        return self._schema_dir

    def validate(self, record_type: RecordType, record: Any) -> None:
        """记录不合规时抛出 ``jsonschema.ValidationError``。"""
        self._validators[record_type].validate(record)

    def iter_errors(self, record_type: RecordType, record: Any) -> list[jsonschema.ValidationError]:
        """返回记录的全部校验错误；合规时为空列表。"""
        return list(self._validators[record_type].iter_errors(record))

    def is_valid(self, record_type: RecordType, record: Any) -> bool:
        return self._validators[record_type].is_valid(record)
