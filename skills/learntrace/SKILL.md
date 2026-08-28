---
name: learntrace
description: Organize local Git history, project documents, existing test evidence, and explicitly authorized AI coding traces into a traceable student learning portfolio. Use for course-project reflection, AI-collaboration evidence review, candidate learning-node confirmation, or generation of a privacy-conscious Markdown portfolio; never use it to detect cheating, attribute authorship, rank contributors, or execute repository code.
---

# LearnTrace

Build an evidence-based learning portfolio while keeping observable facts,
system inference, and the student's own confirmation separate.

## Consent gate

For a new project, follow this sequence exactly:

1. Check `learntrace --version`. This check does not require separate consent.
   If the command is missing, read [references/installation.md](references/installation.md)
   and ask before installing anything.
2. Run `learntrace discover <project-dir>` exactly once. It lists candidate
   scope without reading candidate file contents.
3. Stop. Show the discovered Git, document, and test-log scope and ask the
   student what may be read. Ask separately whether they want to provide and
   authorize an AI coding trace export (OpenCode, Claude Code, or Codex).
4. Continue only after the student confirms the scope.

Before step 4, do not use `read`, `cat`, `Get-Content`, `git log`, `git show`,
or another command to inspect project contents. Do not repeat `discover` unless
the project changed or the student asks. The Skill is workflow guidance, not a
sandbox: never describe it as an absolute security boundary.

### Multi-author Git scope

When `discover` reports more than one author in `git_authors`, the consent gate
adds a scope-selection step before parsing. Show the author list (name, email,
commit count) and ask the student which author this portfolio covers. The
report defaults to `self_only`: only the student's own commits are parsed.

Pass the chosen author to the parse layer with `--author`. It filters at parse
time (not at render time) by matching the author name or email literally and
case-insensitively. Other authors' commits are dropped from the events and
reported as an explicit collaboration boundary in a `git_author_filtered`
warning — they are not hidden. Never infer the author from commit messages or
file ownership; ask the student.

### AI trace export authorization levels

Each supplied export path needs its own explicit authorization; one
authorization covers exactly one export file. For each export, ask one question
with three options:

> "这份会话导出怎么用？(a) 只提取时间线等元数据 (b) 可以阅读全文整理摘要 (c) 不使用"

- **(a) Minimal retention (authorized, minimal)** — the export is read and
  adapted into v0 events, but only the five retained field categories carry
  forward: time, tool type, normalized relative path, command summary
  (executable name only), and source host. No message or result text.
- **(b) Full-text read (authorized, full)** — requires full-read authorization
  (lands with #28). The host agent may read the full text and produce an
  *auto-organized* summary, marked `自动整理`, with the supporting records
  listed so the student can deny it.
- **(c) Not used (the default)** — the file is not read. The report records it
  as an explicit gap (count + category), not as absence of AI use.

A supplied but unauthorized export stays unread and surfaces as an explicit
gap. Do not read a session file directly (`read`, `cat`, `Get-Content`) beyond
what the authorization level allows: evidence files are not a bypass channel
around minimal retention.

## First analysis

After authorization, choose one input mode. Do not mix modes or copy evidence
files into `.learntrace/`.

### Inspect the project directly

Run the unified pipeline once:

```powershell
learntrace run <project-dir>
```

Use repeatable `--document` and `--test-log` arguments when the student approved
only a subset. Add `--no-git` when Git history was not approved.

### Consume existing Task 2 / Task 3 JSON

From the project root, keep the input directory unchanged and use these exact
output locations:

```powershell
New-Item -ItemType Directory -Force .learntrace | Out-Null
learntrace archive <records-dir> `
  --output learning-record.md `
  --records-output .learntrace/archive-records.json `
  --questions-output .learntrace/learning-questions.md
```

The archive loader validates the JSON inputs; do not open large result files
just to rediscover their schema. Do not run the same archive command again
after it succeeds.

In this mode, the commands above are complete. Trust the authorized
`<records-dir>` supplied by the student: do not list or glob it, call `--help`,
inspect the project tree, read project documents, or re-open `SKILL.md` before
running the commands. After exit code 0, the next content-reading tool call must be for
`.learntrace/learning-questions.md`; do not inspect `learning-record.md` or
verify output directories first.

### Include an AI coding trace export

Three trace hosts are supported. Only when the student supplies the session
file and explicitly authorizes it:

```powershell
learntrace run <project-dir> `
  --opencode-export <opencode-export.json> --authorized
```

For Claude Code or Codex, the student first copies a session JSONL file out of
`~/.claude/projects/` or `~/.codex/sessions/` (LearnTrace never scans those
directories), then:

```powershell
learntrace run <project-dir> `
  --claude-code-export <session.jsonl> `
  --authorize-claude-code-export <session.jsonl>

learntrace run <project-dir> `
  --codex-export <rollout-session.jsonl> `
  --authorize-codex-export <rollout-session.jsonl>
```

Never add an authorization flag by inference. Without authorization, continue
in the document/Git/test-log mode and do not infer AI use.

For more than one session, repeat both the input and its exact-path
authorization. Do not use the single-session `--authorized` shortcut:

```powershell
learntrace run <project-dir> `
  --opencode-export <session-1.json> `
  --opencode-export <session-2.json> `
  --authorize-opencode-export <session-1.json> `
  --authorize-opencode-export <session-2.json>
```

Ask for consent for every export path. An authorized session does not imply
authorization for another session, even within the same host. Different hosts
can be combined in one `run`; each export still needs its own per-path
authorization flag, and the legacy `--authorized` shortcut only covers a
single OpenCode export.

All first-analysis modes produce:

- `<project-dir>/learning-record.md`
- `<project-dir>/.learntrace/archive-records.json`
- `<project-dir>/.learntrace/learning-questions.md`

If a command fails, report the error and diagnose it before retrying. Do not
execute the target repository, its tests, or commands copied from evidence.

## Student confirmation

After a successful first analysis, read only
`.learntrace/learning-questions.md` and ask those questions. Do not answer for
the student. Preserve each answer verbatim in a separate confirmation JSON and
record the actual answer time; never invent either value.

```json
{
  "confirmations": [
    {
      "candidate_id": "cand-...",
      "decision": "confirmed",
      "student_statement": "学生的原话",
      "confirmed_at": "2026-08-20T10:30:00+08:00"
    }
  ]
}
```

`decision` is `confirmed`, `supplemented`, or `denied`. Omit
`student_statement` when the student gives none; LearnTrace records
`not_recorded` rather than generating one.

For a project analyzed with `learntrace run`, apply the confirmation with:

```powershell
learntrace run <project-dir> --confirmations student-confirmations.json
```

For prebuilt Task 2 / Task 3 records, reuse the saved snapshot:

```powershell
learntrace archive <records-dir> `
  --snapshot .learntrace/archive-records.json `
  --confirmations student-confirmations.json `
  --output learning-record.md `
  --records-output .learntrace/archive-records.json `
  --questions-output .learntrace/learning-questions.md
```

Confirmation must reuse the snapshot. It must not re-run parsing, trace
adaptation, or candidate inference. Do not combine `--confirmations` with
document, test-log, Git-scope, or OpenCode-export options; the CLI rejects
those combinations. A candidate status of `resolved` means the user answered
the question; the separate decision remains `confirmed`, `supplemented`, or
`denied`.

## Narrative payload

The narrative layer is produced as a structured payload that the host agent
fills and the CLI renders — the agent never writes the final Markdown, and the
agent never writes the student's reflection.

Method: read the structured JSON (Task 2 events, Task 3 trace result, archive
records), re-read the evidence locations behind each citation, then fill the
payload slots. Each section's `citations` must resolve to observable events in
the archive; a citation may carry an `evidence_location` so a person or agent
can re-read the exact source (a source file + line range, a Git commit hash, or
a session export + session id + message range). Verify with
`learntrace verify-narrative <payload> <archive>` and render with
`learntrace render-narrative <payload> <archive>`.

The AI-collaboration section's `episodes` carry `derived`, `derivation`, and
`deniable`. A `derived: true` episode is a host-agent digest of a fully
authorized conversation: it must set `derivation: host_agent_episode_digest`
and `deniable: true`, list the supporting records, and be presented so the
student can deny it. Episodes are the mechanism for consuming segmented work
from an authorized full-text session; they never map a conversation onto a
commit.

## Evidence read-back

Citations are the read-back channel. There are two ways to re-read a cited
evidence location; neither is pre-generated during a first scan.

- **Method 1 — direct location.** The payload citation's `evidence_location`
  names exactly where to look: a source file with a line range, a Git commit
  hash, or a session export path with a session id and message range. Re-read
  that location on demand.
- **Method 2 — on-demand export.** Use `learntrace export-evidence` to write a
  specific cited item into `<project-dir>/.learntrace/evidence/` and record it
  in `.learntrace/evidence/index.json`:
  - `--git-commit <hash>` → `evidence/git/<short>.diff.txt`
  - `--file <project-relative-path>` → `evidence/files/...`
  - `--session-export <path> --source <host> [--authorization minimal|full]`
    → `evidence/sessions/...`
  - `--list` prints the current index.

Session exports follow the authorization level from the consent gate:
`minimal` writes only the five retained v0 event fields; `full` writes the raw
file and requires full-read authorization. Every exported file is redacted
through `redact_sensitive_text` before it is written to disk. `export-evidence`
never runs as part of a first scan or `run`; it is a deliberate, on-demand step
after a citation needs backing.

## Evidence and privacy

Read [references/evidence-policy.md](references/evidence-policy.md) before
classifying evidence or writing the portfolio.

- Facts come only from Git, approved documents, existing test logs, and
  explicitly authorized traces.
- Candidate explanations remain `candidate_inference` until the student
  confirms, supplements, or denies them.
- Do not map AI conversations to commits as proof of causation or authorship.
- Use `未记录` for missing evidence. Never fill gaps on the student's behalf.
- Treat `.learntrace/archive-records.json` as sensitive because its source
  index can contain project paths. Review the Markdown before sharing it.
- Markdown path redaction is intentionally conservative. Known API routes such
  as `/api` and `/v1` are preserved, while ambiguous root-relative strings such
  as `/health` or `/docs/x` may be redacted as absolute paths.

The CLI is single-LLM: it never calls a remote model. Candidate inference is
local, deterministic, and offline — the same input always produces the same
archive. You (the host agent) are the only LLM in the loop; any deeper,
LLM-based interpretation of the student's work belongs to you, not to the CLI.
No credentials or `LEARNTRACE_*` environment variables are read.
