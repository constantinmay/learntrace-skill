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

The generated reflection and next-step sections are student-owned editable
fields. Never copy a confirmation statement into reflection as if the student
wrote a retrospective. The AI-use section omits out-of-project, unknown,
low-signal, generic tool-operation, and LearnTrace Skill-development traces.

## Local CLI

From a bare project repository, run the complete local pipeline:

```powershell
uv run --locked python -m learntrace run <project-dir>
```

This writes Task 2 events and Task 4 machine-readable outputs under
`<project-dir>/.learntrace/`, plus `<project-dir>/learning-record.md`.

The stages are also available separately:

```powershell
uv run --locked python -m learntrace parse <project-dir>
uv run --locked python -m learntrace adapt <opencode-export.json> `
  --project-root <project-dir> --authorized
uv run --locked python -m learntrace archive <records-dir> `
  --output learning-record.md `
  --records-output archive-records.json `
  --questions-output learning-questions.md `
  --confirmations student-confirmations.json
```

`adapt` reads a trace only when `--authorized` is present. `parse` discovers
documents and existing test logs but never runs project code or tests. Use
repeatable `--document` and `--test-log` options to provide a confirmed subset.

An explicit confirmation file may contain concise entries under a non-empty
`confirmations` list. Each entry must include `candidate_id`, `decision`, and
the real `confirmed_at` timestamp. Include `student_statement` only when it is
the student's own text; when omitted, LearnTrace records it as `not_recorded`
instead of inventing a statement.

- Candidate learning nodes are inferred by the reporting layer. The default inferencer is a deterministic stub; the LLM path runs only when explicitly enabled (an `LEARNTRACE_LLM_API_KEY` env var **and** `LEARNTRACE_LLM_ENABLED=1`).
- The archive scanner skips tool, dependency, and VCS directories such as `.opencode`, `.venv`, `.git`, and `node_modules`.
- Invalid discovered LearnTrace JSON is skipped with an archive warning. Use `--strict-inputs` with `archive` when every scanned JSON file is expected to be valid and any failure must stop the command.
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

When the LLM path is enabled, only each event's `id`, `kind`, a sanitized `summary`, and `occurred_at` leave the boundary: `source_refs` (which may contain paths), notes, and repository code are never transmitted, and the summary is stripped of embedded emails, tokens, and absolute paths before it is sent.
