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

Treat Task 2 Git events as navigation into the code evidence, not as a semantic
description of the change. When a commit is important to the final account,
first build the complete local navigation index, then use its full hash and
relevant file paths to inspect the version layout and request original evidence:

```powershell
learntrace git-index <project-dir>
learntrace git-tree <project-dir> <commit-hash>
learntrace git-evidence <project-dir> <commit-hash> --path <repository-path>
learntrace git-file <project-dir> <commit-hash> <repository-path> --lines <start>:<end>
learntrace git-file <project-dir> <commit-hash> <repository-path> --bytes <start>:<end>
```

After Task 2 is available, use these interfaces instead of raw `git log`, `git show`,
`git diff`, or broad repository reads. Search `history.jsonl` with a narrow text query,
then follow its structured commit, tree, path, and line locators. Native Git is only a
declared fallback when the corresponding LearnTrace command fails; record that failure
and the fallback in the evidence account.

Search `.learntrace/evidence/git/history.jsonl` without loading it all into the
conversation. Use its `tree_id`, top-level layout, file roles, and changed paths to
choose evidence; do not treat them as a code summary. Read
`.learntrace/evidence/git/<commit-hash>/index.json` first, then only the artifacts
needed for the investigation. Check every artifact's `available`, `reached_eof`,
`continuation`, and locator fields. Use the hunk manifest to request before/after lines
with `git-file`; follow `next_line` or `next_byte` until the required evidence is read.
Only `reached_eof=true` proves that a file was read to its end. Use byte paging for a
very long single line or when an exact byte sequence matters.

When uncommitted work is relevant, run:

```powershell
learntrace git-worktree <project-dir>
learntrace git-file <project-dir> index <repository-path> --lines <start>:<end>
learntrace git-file <project-dir> worktree <repository-path> --lines <start>:<end>
```

The worktree index does not include untracked file content. Read an authorized file
only when needed. A `same_commit` source/test relation is navigation only and has
`causal=false`; verify the actual assertion and implementation before describing a
connection. Cross-check important code changes with tests, logs, and authorized Task 3
conversation evidence before writing a conclusion. If binary, non-UTF-8, LFS, submodule,
or truncated evidence cannot be read, preserve that gap in the final account.
If history metadata reports `repository_shallow=true` or `history_complete=false`, call
the oldest record only the local visible boundary; do not describe it as initialization.
When aggregate events omit side-branch detail, search the complete
`.learntrace/evidence/git/history.jsonl` index and continue from the selected commit.
If the warning reports `history_index_status=requires_generation`, the path is an
expected output, not an existing locator: run the supplied
`learntrace git-index <project>` command first, then verify that the first metadata
record's `head` equals `history_index_expected_head` before relying on the index.

The export directory contains local evidence locators, a complete hunk manifest, and
bounded previews. Source bodies remain in Git and are read through `git-file`; the
directory must not be committed or shared directly.

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

Remote LLM inference is opt-in and requires both `LEARNTRACE_LLM_API_KEY` and
`LEARNTRACE_LLM_ENABLED=1`. Only event id, kind, sanitized summary, and time are
sent; source references, notes, and repository code stay local. Empty or invalid
LLM results fall back to deterministic local inference and remain visibly
flagged in the archive. `LEARNTRACE_LLM_MAX_TOKENS` optionally controls the
response budget (default `8000`, range `256`–`65536`); a length-limited or
reasoning-only response is reported before fallback.
