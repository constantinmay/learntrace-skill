# OpenCode Trace Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the smallest useful Task 3: read one explicitly authorized OpenCode JSON export, turn safe tool records into Schema v0 `trace_record` events, remove sensitive details, and report why no events exist.

**Architecture:** Keep Schema v0 unchanged. Add a small adapter module, a small privacy helper, one Task 3 result type, and a minimal JSON writer. Do not build a generic multi-Agent framework, discovery service, CLI, Git correlator, or future-version compatibility layer.

**Tech Stack:** Python 3.11 standard library, existing `learntrace.models`, Pytest, Ruff, Pyright.

---

## Task 1: Implement the complete minimal adapter with focused unit tests

**Files:**

- Create: `src/learntrace/adapters/types.py`
- Create: `src/learntrace/adapters/opencode.py`
- Modify: `src/learntrace/adapters/__init__.py`
- Create: `src/learntrace/privacy/redaction.py`
- Modify: `src/learntrace/privacy/__init__.py`
- Create: `tests/unit/adapters/test_opencode.py`
- Create: `tests/unit/privacy/test_redaction.py`

- [ ] **Step 1: Write the focused failing tests**

Cover only M1 acceptance behavior:

- authorization is checked before any file access
- `not_authorized`, `not_provided`, `authorized_not_found`, and `parsed`
- one artificial completed file tool becomes one validated `ObservableEvent(kind=trace_record)`
- stable ID and `trace://opencode/<session>/message/<message>/part/<part>` source ref
- timestamp comes only from `state.time.start`
- malformed sibling records are skipped with a safe warning
- only assistant messages whose session ID matches the export can produce tool events
- pending/running records are skipped unless the containing assistant message has a persisted error
- chat, prompts, tool output, error body, full commands, secrets, and project-external paths never enter summaries
- project-local paths become relative POSIX paths
- invalid JSON or incompatible root structure raises `UnsupportedOpenCodeFormatError`
- a structurally compatible unverified application version parses with a warning

Privacy helper tests only need representative cases:

- password/token/Bearer/credential URL redaction
- project-local, absolute external, and traversal paths
- `git status`, `uv run pytest`, and a generic command summary
- redaction is idempotent

- [ ] **Step 2: Confirm the tests fail for missing implementation**

Run:

```powershell
uv run pytest tests/unit/adapters/test_opencode.py tests/unit/privacy/test_redaction.py -q
```

- [ ] **Step 3: Implement the smallest passing code**

Public API:

```python
adapt_opencode_export
TraceAdapterResult
TraceInputStatus
TraceParseIssue
UnsupportedOpenCodeFormatError
```

Use these rules:

- accept only a caller-provided JSON file; never discover OpenCode storage
- cap input at 32 MiB
- require the documented `info` and `messages` skeleton
- process only `type="tool"` parts
- use strict field whitelists: file tools may keep a safe relative path, Bash may keep only a conservative command verb summary, and Task remains generic
- never execute a command or inspect ignored text/output fields
- validate every event with `ContractValidator`
- sort events and warnings deterministically
- return zero events for every no-trace state

- [ ] **Step 4: Run focused tests and static checks**

```powershell
uv run pytest tests/unit/adapters/test_opencode.py tests/unit/privacy/test_redaction.py -q
uv run ruff check src/learntrace/adapters src/learntrace/privacy tests/unit/adapters tests/unit/privacy
uv run ruff format --check src/learntrace/adapters src/learntrace/privacy tests/unit/adapters tests/unit/privacy
uv run pyright src/learntrace/adapters src/learntrace/privacy tests/unit/adapters tests/unit/privacy
git diff --check
```

## Task 2: Add one realistic artificial fixture, minimal output writing, and a short handoff

**Files:**

- Create: `src/learntrace/adapters/serialization.py`
- Create: `tests/unit/adapters/test_serialization.py`
- Create: `tests/fixtures/opencode/authorized-export.json`
- Create: `tests/integration/test_opencode_adapter_flow.py`
- Create: `docs/opencode-adapter.md`

- [ ] **Step 1: Write one artificial fixture and failing integration test**

The fixture contains a safe file call, bash call, generic task delegation, one malformed part, and explicit fake forbidden markers in chat/description/subagent/prompt/output/error fields.

The test adapts and writes the result, then proves:

- output has valid v0 `trace_record` events and a safe warning
- none of the forbidden markers appears anywhere in serialized JSON
- no raw personal path or full command remains
- a valid no-tool export returns `authorized_not_found` and zero events

- [ ] **Step 2: Implement a minimal safe writer**

`write_trace_result(result, output_path)`:

- validates events again
- writes UTF-8 JSON to a unique temporary file in the target directory
- atomically replaces the destination
- removes its own temporary file on failure

Add only a happy-path test and a replacement-failure cleanup test. Do not build a general storage subsystem.

- [ ] **Step 3: Write a short Task 4 handoff**

Document:

- `opencode export <sessionID> > opencode-session.json`
- caller-provided path plus explicit authorization
- four statuses
- exact information retained and removed
- `events` is the only stable cross-task interface
- no command execution, chat storage, candidate generation, or Git correlation

- [ ] **Step 4: Run the Task 3 test set**

```powershell
uv run pytest tests/unit/adapters tests/unit/privacy tests/integration/test_opencode_adapter_flow.py -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright
git diff --check
```

## Task 3: Verify once, review once, and open the PR

- [ ] **Step 1: Run repository-wide checks**

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pre-commit run --all-files
git diff --check
```

- [ ] **Step 2: Perform one independent final review**

Review only for blocking issues in:

- authorization-before-access
- Schema v0 compliance
- privacy leaks
- command non-execution
- malformed-record degradation
- scope conflicts with Task 2/Task 4

Fix confirmed defects with regression tests. Do not expand scope for optional refinements.

- [ ] **Step 3: Commit and deliver**

Use a small number of conventional commits, create/link the Task 3 Issue, push `feature/opencode-adapter`, and open a PR containing the check results. Do not merge without another maintainer's approval.
