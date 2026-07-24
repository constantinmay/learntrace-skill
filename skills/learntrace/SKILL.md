---
name: learntrace
description: Organize local Git history, project documents, existing test evidence, and explicitly authorized AI coding traces into a traceable student learning portfolio. Use for course-project reflection, AI-collaboration evidence review, candidate learning-node confirmation, or generation of a privacy-conscious Markdown portfolio; never use it to detect cheating, attribute authorship, rank contributors, or execute repository code.
---

# LearnTrace

Build a local, evidence-based learning portfolio while separating observation, student confirmation, and uncertain inference.

## Workflow

1. Discover candidate local inputs: Git metadata, UTF-8 Markdown or text documents, existing test logs, and any AI trace the student explicitly authorizes.
2. Ask the student to confirm the analysis scope before reading optional traces or sensitive material.
3. Use bundled parsing scripts when available. Never execute the target repository, its tests, or commands copied from logs and traces.
4. Record source references for every observable event.
5. Propose candidate learning nodes from observable events, including follow-up questions, rejected or revised AI suggestions, failed approaches, added tests, and changed constraints.
6. Ask only the questions needed for the student to confirm, supplement, or deny candidates.
7. Generate an editable local Markdown portfolio containing goals, AI-use scenarios, decisions, validation evidence, reflection, and next steps.

## Local CLI

Use the local Python pipeline for Task 4 archives:

```powershell
python -m learntrace.archive <project-dir> `
  --output learning-record.md `
  --records-output archive-records.json `
  --questions-output learning-questions.md
```

- The default inferencer is a deterministic stub; do not look for LLM keys or log in.
- The CLI skips common dependency and VCS directories such as `.venv`, `.git`, and `node_modules`.
- Add `--strict-inputs` when the input directory should contain only LearnTrace JSON records.
- The JSON archive includes a stable SHA-256 manifest fingerprint for review and reproducibility.

## Evidence rules

Read [references/evidence-policy.md](references/evidence-policy.md) before classifying or writing evidence.

- Label direct repository, document, log, or authorized-trace evidence as observable fact.
- Label statements explicitly confirmed by the student as student confirmation.
- Label explanations inferred by the system as candidate inference, including basis and uncertainty.
- Use `未记录` when evidence is missing. Do not fill gaps.
- Do not infer a mapping between AI conversations and Git commits or treat commit authorship as proof of personal understanding.

## Privacy

Minimize trace data by default. Retain only time, tool type, file path, command summary, and source host unless the student explicitly authorizes full conversation content. Keep the portfolio local unless the student requests an export, and redact the export before sharing.
