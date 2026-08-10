# OpenCode Trace Adapter Design

## 1. Goal

Implement LearnTrace Task 3 as a deterministic, local-only adapter for a user-provided OpenCode JSON export. The adapter converts authorized OpenCode tool-call records into Schema v0 `ObservableEvent` objects with `kind="trace_record"`, applies privacy filtering before any summary leaves the adapter, and reports why no events were produced without inventing a fake event.

The implementation must not execute commands, inspect an OpenCode private database, infer learning conclusions, or connect an AI trace to a Git commit.

## 2. Selected input approach

Three possible OpenCode inputs were considered:

1. **User-provided `opencode export` JSON — selected.**
   - OpenCode officially supports `opencode export [sessionID]`.
   - The export preserves session, message, part, status, and time identifiers.
   - The user chooses the session and explicitly provides the resulting file.
   - LearnTrace does not need permission to search an entire OpenCode data store.

2. **Direct SQLite access — rejected for M1.**
   - It would expose all sessions in the user's global OpenCode store.
   - The internal schema is an implementation detail and changes independently of LearnTrace.
   - Safe concurrent database access and platform-specific storage discovery add no value to the first adapter.

3. **Markdown export — rejected for M1.**
   - Markdown does not provide stable per-record identifiers or reliable per-tool timestamps.
   - Fences, separators, injected system text, and nested code blocks make deterministic parsing fragile.

M1 is locked with fixtures from OpenCode `v1.18.6`. `info.version` is the OpenCode application version, not an export-schema version, so a different application major is accepted only when the required export structure is still present and produces an `unverified_opencode_version` warning. Unknown root structures are rejected as a batch instead of being guessed.

## 3. Module boundaries

```text
src/learntrace/
├─ adapters/
│  ├─ __init__.py
│  ├─ opencode.py
│  ├─ serialization.py
│  └─ types.py
└─ privacy/
   ├─ __init__.py
   └─ redaction.py
```

- `adapters/opencode.py` understands the OpenCode export shape and constructs validated `ObservableEvent` objects.
- `adapters/types.py` defines Task 3 batch status, safe parse warnings, and the result container. It does not change Schema v0.
- `adapters/serialization.py` validates every event and writes the Task 3 result with a unique temporary file and atomic replacement.
- `privacy/redaction.py` contains host-independent secret redaction, path normalization, and command summarization.

Task 3 will not modify the shared Schema, Task 2 parsers, Task 4 reporting code, or the existing CLI.

## 4. Public API

`learntrace.adapters` exports:

```python
adapt_opencode_export
write_trace_result
TraceAdapterResult
TraceInputStatus
TraceParseIssue
UnsupportedOpenCodeFormatError
```

The main entry point is:

```python
def adapt_opencode_export(
    export_path: Path | None,
    *,
    authorized: bool,
    project_root: Path | None = None,
    validator: ContractValidator | None = None,
) -> TraceAdapterResult:
    ...
```

Authorization is checked before file existence or file contents. If `authorized` is false, the function returns `not_authorized` without touching `export_path`.

`project_root` is optional and is used only to turn project-local paths into relative POSIX paths. It is never used to discover a trace.

## 5. Batch result

Schema v0 defines individual evidence records but does not define a batch parse report. Task 3 therefore uses a local container:

```python
class TraceInputStatus(StrEnum):
    NOT_PROVIDED = "not_provided"
    NOT_AUTHORIZED = "not_authorized"
    AUTHORIZED_NOT_FOUND = "authorized_not_found"
    PARSED = "parsed"
```

```python
@dataclass(frozen=True, slots=True)
class TraceParseIssue:
    code: str
    location: str
    message: str
```

```python
@dataclass(frozen=True, slots=True)
class TraceAdapterResult:
    status: TraceInputStatus
    events: tuple[ObservableEvent, ...] = ()
    warnings: tuple[TraceParseIssue, ...] = ()
```

Its JSON representation is:

```json
{
  "adapter_version": "v0",
  "status": "parsed",
  "events": [],
  "warnings": []
}
```

Only `events` is the stable cross-task data interface. The container is owned by Task 3 and does not pretend to be a Schema v0 record.

Status rules:

- `authorized=False` → `not_authorized`, zero events, no input access.
- `authorized=True` and `export_path=None` → `not_provided`, zero events.
- Authorized path missing, not a regular file, or valid export containing no usable tool records → `authorized_not_found`, zero events.
- At least one valid tool record → `parsed`, even if malformed sibling records produced warnings.

## 6. Supported OpenCode export profile

The adapter requires this batch skeleton:

```json
{
  "info": {
    "id": "ses_...",
    "version": "1.18.6"
  },
  "messages": [
    {
      "info": {
        "id": "msg_...",
        "sessionID": "ses_...",
        "time": {"created": 0}
      },
      "parts": []
    }
  ]
}
```

Unknown extra fields are ignored. The root, `info`, `info.id`, `info.version`, and `messages` must have the expected types. Session, message, and part IDs must match the short ASCII profile `[A-Za-z0-9][A-Za-z0-9._~-]{0,127}`; IDs are rejected rather than truncated so source references remain traceable and bounded. An invalid batch session ID rejects the export, while an invalid message or part ID skips only that record with a safe warning. A version other than major `1` does not prove the export is incompatible; it adds a warning while structural checks remain authoritative.

Only parts with `type="tool"` are evidence inputs. User text, assistant text, reasoning, system reminders, file contents, patches, snapshots, and tool outputs are ignored by construction.

A message can contain evidence inputs only when:

- the message and `message.info` are objects
- `message.info.id` matches the short ASCII ID profile
- `message.info.sessionID` matches the top-level session ID
- `message.info.role` is exactly `assistant`
- `message.parts` is an array

Other roles are ignored. Malformed assistant messages are skipped with `invalid_message`. A persisted incomplete-tool decision uses only the presence of a non-null `message.info.error`; its error body is never read or copied.

A supported tool part must provide:

- `id` matches the short ASCII ID profile
- matching `sessionID`
- `messageID` matching the containing message
- non-empty `tool`
- `state` object
- `state.status` in `pending`, `running`, `completed`, or `error`
- `state.input` object

`completed` and `error` parts become normal events. A `pending` or `running` part becomes an incomplete event only when its containing assistant message has a persisted error; otherwise it is skipped with an `incomplete_tool_part` warning because the export may have been taken while the session was still live.

Malformed messages or tool parts are skipped with a safe warning. Known non-tool parts are intentionally ignored. Unknown part types are skipped with an `unknown_part_type` warning. Duplicate `(session ID, message ID, part ID)` records keep the first occurrence and add a warning.

Before returning, events are sorted by `(missing timestamp, occurred_at, source ref)`: timestamped events appear first in chronological order, equal timestamps are broken by source reference, and events without a timestamp appear last in source-reference order. Warnings are sorted by `(location, code, message)`. The same input therefore produces byte-for-byte stable serialized output.

## 7. Event identity and provenance

One valid OpenCode tool part becomes one `ObservableEvent`.

The source reference is:

```text
trace://opencode/<percent-encoded-session-id>/message/<percent-encoded-message-id>/part/<percent-encoded-part-id>
```

The event ID is:

```text
evt-trace-<first-16-hex-of-sha256(source-ref)>
```

This ID does not depend on array order and does not collide with Task 2's event namespaces.

Each event uses:

```python
ObservableEvent(
    id=event_id,
    kind=EventKind.TRACE_RECORD,
    summary=summary,
    source_refs=(
        SourceRef(
            type=SourceType.TRACE_RECORD,
            ref=source_ref,
            note="opencode",
        ),
    ),
    occurred_at=occurred_at,
)
```

Every event is validated with `ContractValidator.validate("observable_event", event.to_dict())` before it enters the result.

## 8. Time handling

Time is taken only from `state.time.start`. If it is absent, `occurred_at` is omitted. If it is present but is not a finite, non-negative Unix timestamp in milliseconds, `occurred_at` is omitted and an `invalid_timestamp` warning is added.

Valid timestamps are converted to RFC 3339 UTC with a `Z` suffix. File modification time and the current clock are never used as substitutes.

## 9. Neutral summaries

Every summary begins with a normalized tool name and the observed state:

- `completed` → `OpenCode 工具 <tool> 已完成。`
- `error` → `OpenCode 工具 <tool> 以错误结束。`
- incomplete part with a persisted message error → `OpenCode 工具 <tool> 在消息错误结束时未完成。`

Optional safe details are appended from a strict whitelist:

- File tools (`read`, `edit`, `write`, `apply_patch`) may include a normalized `filePath` or `path`.
- `bash` may include only a conservative command verb summary derived from `command`; `workdir`, arguments, payloads, and outputs are not retained.
- `task` remains generic. It records only that the task tool reached the observed state and never includes `description`, `subagent_type`, `prompt`, child output, or inferred child actions.
- Other tools remain generic and do not contribute input fields.

Summaries never include tool output, error messages, old/new file contents, prompts, reasoning, patches, or metadata.

Tool names are restricted to a short safe character set. Invalid names are represented as `unknown-tool`, preventing Markdown or control-character injection.

## 10. Privacy behavior

`privacy/redaction.py` provides three focused operations:

```python
redact_sensitive_text(value: str, *, limit: int = 160) -> str
normalize_project_path(value: str, project_root: Path | None) -> str
summarize_command(value: str) -> str
```

### Secret redaction

The redactor covers common deterministic secret shapes:

- `password`, `passwd`, `pwd`, `token`, `api_key`, `apikey`, `secret` assignments
- `Authorization: Bearer ...`
- credential-bearing URLs
- well-known token prefixes such as `sk-`, `ghp_`, `github_pat_`, and `xox[baprs]-`
- AWS access-key-shaped identifiers
- private-key blocks

Values are replaced with `[REDACTED]`. Redaction is idempotent and applied before length limiting.

### Path normalization

- Safe project-relative paths are returned as POSIX paths.
- Absolute paths inside an explicitly supplied `project_root` become project-relative.
- Absolute paths outside the project become `[outside-project]`.
- Absolute paths with no supplied root become `[absolute-path]`.
- Relative paths containing `..`, control characters, or empty normalized results become `[unsafe-path]`.
- Windows drive, UNC, and POSIX paths are recognized independent of the host running LearnTrace.

The OpenCode session directory is never emitted.

### Command summaries

The adapter never copies a complete command. It derives only conservative command verbs from `command`, such as `git status`, `pytest`, `npm test`, or the first safe executable name. Quoted payloads, environment assignments, redirects, pipes, URLs, paths, and secret-looking arguments are omitted.

## 11. Error handling

Batch-level failures raise `UnsupportedOpenCodeFormatError` with a safe message that contains no absolute input path:

- export larger than 32 MiB
- invalid UTF-8
- invalid JSON
- wrong root type
- missing or malformed required batch fields

Record-level failures do not reject valid siblings. They produce `TraceParseIssue` entries with locations such as `messages[2].parts[4]`, never filesystem paths or raw source fragments.

Warnings use stable codes:

- `invalid_message`
- `invalid_tool_part`
- `duplicate_tool_part`
- `invalid_timestamp`
- `incomplete_tool_part`
- `unknown_part_type`
- `unverified_opencode_version`

The adapter does not catch programmer errors or Schema validation failures as source warnings.

## 12. Serialization

`write_trace_result`:

1. converts the result to a dictionary
2. validates every event again
3. serializes UTF-8 JSON with `ensure_ascii=False`
4. creates a unique temporary file in the output directory with exclusive creation
5. flushes and closes it
6. atomically replaces the requested output
7. removes the temporary file if serialization or replacement fails

It does not use a predictable `.<name>.tmp` path and does not follow a pre-created temporary symlink.

## 13. Test strategy

Tests use only artificial, sanitized OpenCode-like exports.

### Privacy unit tests

- assignment, bearer, URL, token-prefix, AWS-key, and private-key redaction
- idempotent redaction
- Windows and POSIX project-relative path conversion
- absolute external path removal
- traversal rejection
- conservative shell-command summaries

### Adapter unit tests

- authorization checked before any file operation
- missing input statuses
- normal file, bash, task, and error tool parts
- wrong-role and session-mismatched messages ignored or warned without producing events
- pending/running parts skipped while live and retained as incomplete facts when the message has a persisted error
- source references, stable IDs, stable ordering, and RFC 3339 time
- sanitized OpenCode export compatibility
- text/system/reasoning/tool-output exclusion
- partial malformed records with warnings
- duplicate part handling
- no-tool export degradation
- file-size and invalid JSON rejection
- structurally compatible unverified version warning and incompatible structure rejection
- every produced event passes the Schema v0 validator

### Serialization tests

- exact batch JSON shape
- valid UTF-8 output
- atomic replacement
- pre-existing predictable symlink cannot redirect the write

### Integration test

A fixture containing text noise, tool calls, secrets, project-local paths, an external path, and a malformed sibling record is adapted and written. The final file must contain valid `trace_record` events and warnings, while a forbidden-value list proves that secrets, personal paths, chat text, prompts, tool output, and system reminders are absent.

## 14. Non-goals

- OpenCode database discovery or direct SQLite reads
- Markdown transcript parsing
- Claude Code or other Agent adapters
- full chat extraction or storage
- LLM-based summaries
- command execution or replay
- candidate learning-node generation
- student confirmation
- Git-to-trace correlation
- CLI orchestration
- Schema v0 changes
