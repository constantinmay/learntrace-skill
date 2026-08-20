# Task 4 LLM Demo

This demo feeds Task 4 a small, hand-authored `ObservableEvent` bundle that has
enough evidence for the deterministic stub and a real LLM to propose
learning-node candidates.

It exercises the archive and candidate-inference portion of LearnTrace. It is
not a substitute for the end-to-end OpenCode Skill check described in the root
README: that check additionally covers discovery, consent, command selection,
and the student-question handoff.

The bundle includes:

- `trace_record`: an authorized AI suggestion to parse roster rows with a regex.
- `test_log`: an existing pytest failure showing quoted comma fields break parsing.
- `document`: a design note requiring CSV-compatible roster imports.
- `git_commit`: a later implementation change replacing the regex with a state-machine parser and adding edge-case tests.

Run with real LLM credentials already present in the environment:

```powershell
.\examples\task4-llm-demo\run-demo.ps1
```

If Windows blocks local PowerShell scripts, run the same demo with a one-shot
execution-policy bypass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\examples\task4-llm-demo\run-demo.ps1
```

The script reads `LEARNTRACE_LLM_API_KEY`, `LEARNTRACE_LLM_BASE_URL`, and
`LEARNTRACE_LLM_MODEL` from the environment, and sets `LEARNTRACE_LLM_ENABLED=1`
so the CLI uses the remote model. LLM inference is opt-in: without
`LEARNTRACE_LLM_ENABLED=1` the CLI falls back to the deterministic stub even
when an API key is present. When LLM inference is enabled, the CLI sends only
each event's `id`, `kind`, a sanitized `summary`, and `occurred_at`;
`source_refs` (potential paths), notes, and repository code are never
transmitted, and any email, token, or absolute path embedded in a summary is
stripped before the request leaves the boundary.
If the remote model fails or returns no valid candidates, the archive records
the reason and falls back to the deterministic local inferencer.

Generated files are written under `examples/task4-llm-demo/out/`, which is
ignored by git so demo outputs do not become source-controlled artifacts.

The first run produces one deterministic pending question when the stub is in
use. To demonstrate the second stage without re-running inference, apply the
clearly synthetic answer in `student-confirmations.json.example`:

```powershell
uv run --locked python -m learntrace archive `
  examples/task4-llm-demo `
  --snapshot examples/task4-llm-demo/out/archive-records.json `
  --confirmations examples/task4-llm-demo/student-confirmations.json.example `
  --output examples/task4-llm-demo/out/confirmed-learning-record.md `
  --records-output examples/task4-llm-demo/out/confirmed-archive-records.json
```

The example statement is test data, not a real student's reflection.
