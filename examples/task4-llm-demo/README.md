# Task 4 LLM Demo

This demo feeds Task 4 a small, hand-authored `ObservableEvent` bundle that has
enough evidence for a real LLM to propose learning-node candidates.

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

The script only reads `LEARNTRACE_LLM_API_KEY`, `LEARNTRACE_LLM_BASE_URL`, and
`LEARNTRACE_LLM_MODEL` from the environment. If the API key is absent, the CLI
falls back to the deterministic stub.

Generated files are written under `examples/task4-llm-demo/out/`, which is
ignored by git so demo outputs do not become source-controlled artifacts.
