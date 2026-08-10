"""Small, atomic JSON writer for Task 3 adapter results."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from learntrace.adapters.types import TraceAdapterResult
from learntrace.models import ContractValidator

_ADAPTER_VERSION = "v0"


def _result_dict(result: TraceAdapterResult) -> dict[str, object]:
    validator = ContractValidator()
    events: list[dict[str, object]] = []
    for event in result.events:
        event_data = event.to_dict()
        validator.validate("observable_event", event_data)
        events.append(event_data)

    warnings: list[dict[str, str]] = [
        {
            "code": warning.code,
            "location": warning.location,
            "message": warning.message,
        }
        for warning in result.warnings
    ]
    return {
        "adapter_version": _ADAPTER_VERSION,
        "status": str(result.status),
        "events": events,
        "warnings": warnings,
    }


def write_trace_result(result: TraceAdapterResult, output_path: Path) -> None:
    """Validate and atomically write one Task 3 result as UTF-8 JSON."""

    payload = json.dumps(
        _result_dict(result),
        ensure_ascii=False,
        indent=2,
    )
    descriptor, temp_name = tempfile.mkstemp(
        dir=output_path.parent,
        prefix=f".{output_path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with handle:
            handle.write(payload)
            handle.write("\n")
            handle.flush()
        os.replace(temp_path, output_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
