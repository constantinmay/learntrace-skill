# Task 4 Demo

This demo feeds Task 4 a small, hand-authored and explicitly synthetic
`ObservableEvent` bundle that has enough evidence for the local deterministic inferencer to propose a
`fix_failed_approach` candidate: the failing pytest log is bound to the later
commit that fixes the same parser.

Unlike the larger presentation-only examples, this directory includes the
small source materials referenced by the bundle under `source-materials/`:

- `docs/import-design.md`: the quoted-field requirement;
- `logs/pytest-roster-import.log`: the failing pytest output;
- `traces/roster-import-ai.jsonl`: a sanitized demonstration conversation excerpt;
- `git/7d3c9f1.patch`: the corresponding parser and test change.

These files let a reviewer inspect every human-readable claim in this demo.
They are demonstration data, not a real student's project or learning outcome.

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

The AI-suggestion record in `parse-result.json` and the commit are deliberately **not** paired
automatically: word similarity and time proximity are not a legitimate
trace-to-commit link, and the deterministic inferencer therefore emits no
`revise_ai_suggestion` candidate for them. The single candidate comes from the
verifiable failing-log → fixing-commit link instead. (Establishing the AI
suggestion → commit link belongs to a fully authorized host-agent
conversation or the student's own confirmation, not to deterministic
inference.)

The archive command also deliberately ignores that bare `trace_record`: a
record embedded in an ordinary JSON bundle is not proof that its source was
authorized through Task 3. The resulting warning demonstrates the boundary;
the source excerpt is included only so the reviewer can inspect the scenario.

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
.\examples\task4-demo\confirm-demo.ps1
```

The example statement is test data, not a real student's reflection.

## Five-step walkthrough

1. **Raw materials:** inspect the four files under `source-materials/`.
2. **Observable facts:** inspect `parse-result.json` for the normalized events and source refs.
3. **Candidate:** run the script and inspect `out/learning-questions.md`; only the failing-log → fixing-commit relationship becomes a candidate.
4. **Student answer:** inspect `student-confirmations.json.example`; it is visibly labeled as synthetic.
5. **Final portfolio:** apply the confirmation command above and inspect `out/confirmed-learning-record.md`. The confirmed statement enters the portfolio while the unsupported AI-suggestion → commit relationship does not.
