# Task 4 Demo

This demo feeds Task 4 a small, hand-authored `ObservableEvent` bundle that has
enough evidence for the local deterministic inferencer to propose a
`fix_failed_approach` candidate: the failing pytest log is bound to the later
commit that fixes the same parser.

LearnTrace is single-LLM: the CLI never calls a remote model. Candidate
inference here is fully deterministic and offline — the same input always
produces the same archive. Deeper, LLM-based understanding of a student's work
belongs to the host agent (OpenCode, Claude Code, …), not to the CLI.

It exercises the archive and candidate-inference portion of LearnTrace. It is
not a substitute for the end-to-end OpenCode Skill check described in the root
README: that check additionally covers discovery, consent, command selection,
and the student-question handoff.

The bundle includes:

- `trace_record`: an authorized AI suggestion to parse roster rows with a regex.
- `test_log`: an existing pytest failure showing quoted comma fields break parsing.
- `document`: a design note requiring CSV-compatible roster imports.
- `git_commit`: a later implementation change replacing the regex with a state-machine parser and adding edge-case tests.

The AI-suggestion trace and the commit are deliberately **not** paired
automatically: word similarity and time proximity are not a legitimate
trace-to-commit link, and the deterministic inferencer therefore emits no
`revise_ai_suggestion` candidate for them. The single candidate comes from the
verifiable failing-log → fixing-commit link instead. (Establishing the AI
suggestion → commit link belongs to a fully authorized host-agent
conversation or the student's own confirmation, not to deterministic
inference.)

Run:

```powershell
.\examples\task4-demo\run-demo.ps1
```

If Windows blocks local PowerShell scripts, run the same demo with a one-shot
execution-policy bypass:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\examples\task4-demo\run-demo.ps1
```

Generated files are written under `examples/task4-demo/out/`, which is ignored
by git so demo outputs do not become source-controlled artifacts.

The first run produces one pending question. To demonstrate the second stage
without re-running inference, apply the clearly synthetic answer in
`student-confirmations.json.example`:

```powershell
uv run --locked python -m learntrace archive `
  examples/task4-demo `
  --snapshot examples/task4-demo/out/archive-records.json `
  --confirmations examples/task4-demo/student-confirmations.json.example `
  --output examples/task4-demo/out/confirmed-learning-record.md `
  --records-output examples/task4-demo/out/confirmed-archive-records.json
```

The example statement is test data, not a real student's reflection.
