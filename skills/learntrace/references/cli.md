# CLI command reference

Flag-level reference for every LearnTrace subcommand. The skill body covers
the workflow; this file is for looking up exact flags.

## Consent stage

### `learntrace --version`

Prints the version. No consent required.

### `learntrace discover <project_dir>`

Lists the candidate scope (Git authors, documents, test logs) without reading
candidate file contents. Run exactly once per project; see the consent gate.

## First analysis

### `learntrace run <project_dir> [options]`

Unified pipeline: parse static evidence, adapt authorized trace exports,
infer candidates, build the archive, render the report.

| flag | meaning |
|---|---|
| `--document D` | include this document (repeatable) |
| `--test-log L` | include this test log (repeatable) |
| `--no-git` | skip Git history |
| `--author A` | parse-layer author filter: full (not substring), case-insensitive match on author name or email |
| `--max-commits N` | side-branch detail budget only; the first-parent chain and merge commits are never removed; by default the complete reachable history is retained |
| `--find-copies-harder` | search harder for duplicate/copy files |
| `--opencode-export P` | OpenCode session export (repeatable) |
| `--authorized` | legacy shortcut: authorizes exactly one OpenCode export |
| `--authorize-opencode-export P` | per-path OpenCode authorization (repeatable; use this for multiple sessions) |
| `--claude-code-export P` / `--authorize-claude-code-export P` | Claude Code session JSONL: input + per-path authorization |
| `--codex-export P` / `--authorize-codex-export P` | Codex rollout session JSONL: input + per-path authorization |
| `--confirmations F` | apply a confirmations JSON (repeatable); the CLI rejects combining it with document / test-log / Git-scope / export options |
| `-o OUT` | output path override |

Outputs at the project root: `learning-record.md`,
`learning-questions.md`; under `.learntrace/`: `task2-result.json`,
`task3-result.json`, `archive-records.json`, `generated-artifacts.json`.

### `learntrace parse <project_dir> [options]`

Builds the static evidence layer only (same `--document` / `--test-log` /
`--no-git` / `--author` / `--max-commits` / `--find-copies-harder` flags as
`run`, plus `-o OUT`). Produces the Task 2 result JSON without trace
adaptation or rendering.

### `learntrace adapt <export_path>... [options]`

Adapts AI trace exports into v0 events only: `--source opencode|claude-code|codex`
(default `opencode`), `--project-root R`, `--authorized`,
`--authorize-export P` (repeatable), `-o OUT`.

### `learntrace archive [records_dir] [options]`

Consumes prebuilt Task 2 / Task 3 JSON (archive-consume mode):
`-o OUT` (report), `--records-output R`, `--questions-output Q`,
`--confirmations F` (repeatable; with `--snapshot S`),
`--trace-result T` (explicit validated Task 3 result JSON, never
discovered by scanning), `--strict-inputs`. Trust
the supplied records directory; do not glob or inspect it (see the skill
body).

## Git evidence navigation

Use these after Task 2 instead of raw `git log` / `git show` / `git diff`:

### `learntrace git-index <project_dir> [-o OUT]`

Builds/refreshes the complete local navigation index
(`.learntrace/evidence/git/history.jsonl`). Search it with a narrow query;
do not load it wholesale into the conversation.

### `learntrace git-tree <project_dir> <revision> [-o OUT]`

Top-level layout of a revision's tree (`revision` = commit hash, `index`, or
`worktree`).

### `learntrace git-evidence <project_dir> <commit> [--path P]... [--max-chars N] [-o OUTDIR]`

Writes the commit's hunk manifest and bounded previews for the given paths.
Read `.learntrace/evidence/git/<commit>/index.json` first; check
`available`, `reached_eof`, `continuation`, and locator fields on every
artifact.

### `learntrace git-file <project_dir> <revision> <path> [--lines S:E | --bytes S:E] [-o OUT]`

Paged read of a file at a revision (`<hash>`, `index`, or `worktree`). Follow
`next_line` / `next_byte` until the required range is read; only
`reached_eof=true` proves the end was reached. Use byte paging for a very
long single line or when an exact byte sequence matters.

### `learntrace git-worktree <project_dir> [--max-chars N] [-o OUT]`

Indexes uncommitted work. Does not include untracked file content.

## Narrative layer

### `learntrace verify-narrative <payload_path> <archive_path>`

Validates the payload against schema v0 and the archive; prints violations.
Must pass before rendering.

### `learntrace render-narrative <payload_path> <archive_path> [-o OUT] [--variant working|submitted]`

Verifies the final payload (after any `--variant` override) and renders the
Markdown.

## Evidence read-back (on-demand)

### `learntrace export-evidence <project_dir> [options]`

Exports one cited item into `.learntrace/evidence/` and records it in
`.learntrace/evidence/index.json`:

- `--git-commit C` → `evidence/git/<short>.diff.txt`
- `--file F` (project-relative; `.git/`, `.learntrace/`, and LearnTrace's own
  outputs are refused) → `evidence/files/...`
- `--session-export S --source host [--authorization minimal|full]` →
  `evidence/sessions/...`
- `--list` prints the current index.

`minimal` writes only the five retained v0 event fields; `full` reads the
raw session file and requires a matching per-file full-read authorization
record. `export-evidence` is never part of a first scan or `run`; it is a
deliberate step after a citation needs backing.

### `learntrace authorize-full-read <project_dir> --session-export S --source host --authorized-at ISO8601`

Records the student's explicit full-read consent for exactly one session
export file. Writes one record (absolute path + host + timestamp, no file
content) to `.learntrace/full-read-authorizations.json`. The flag alone never
grants the right to read; the CLI verifies the record before any full read.
