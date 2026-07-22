"""Task2 批量结果的本地 JSON 写出。"""

from __future__ import annotations

import json
from pathlib import Path

from learntrace.models import ContractValidator
from learntrace.parsers.types import ParseResult


def write_parse_result(
    result: ParseResult,
    output_path: Path,
    *,
    validator: ContractValidator | None = None,
) -> None:
    """以 UTF-8 JSON 写出已校验结果；调用方负责选择本地输出路径。"""
    data = result.to_dict(validator)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output_path)
