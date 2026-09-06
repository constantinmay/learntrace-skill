# Installation and host setup

LearnTrace has two cooperating parts: this Agent Skill and the `learntrace`
Python CLI. Installing only `SKILL.md` does not install the executable.

## Install the CLI from a checked-out release

From the LearnTrace repository, install the command with `uv`:

```powershell
uv tool install .
learntrace --version
```

Re-run `uv tool install --reinstall .` from a newer checkout to rebuild and
replace the local installation. During LearnTrace development, repository
maintainers may instead run `uv run --locked python -m learntrace`; do not use
that source-tree command from the student project being analyzed.

## Make the Skill discoverable in Claude Code

Copy the complete `learntrace` directory, including `references/`, to one of
Claude Code's Skill roots:

- project-local: `.claude/skills/learntrace/`
- personal: `~/.claude/skills/learntrace/`

For a personal installation on Windows PowerShell, run these commands from the
LearnTrace repository:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\learntrace"
Copy-Item ".\skills\learntrace\*" `
  "$env:USERPROFILE\.claude\skills\learntrace" -Recurse -Force
```

Start Claude Code in the student project and invoke `/learntrace`, for example:

```text
/learntrace Create an evidence-backed learning record for this project.
```

Claude Code supplies the model, conversation, and tool approval UI. The local
`learntrace` CLI supplies deterministic evidence processing. Invoking the Skill
does not authorize access to Claude Code's saved session history. A session
JSONL must be copied into an explicitly chosen location and separately approved
with `--authorize-claude-code-export` before LearnTrace reads it.

If the top-level `.claude/skills/` directory was created while Claude Code was
already running and the Skill is not shown, restart Claude Code once. See
<https://code.claude.com/docs/en/skills> for the current discovery rules.

## Make the Skill discoverable in OpenCode

Place the `learntrace` directory under one of OpenCode's supported Skill roots:

- project-local: `.opencode/skills/learntrace/`
- global: `~/.config/opencode/skills/learntrace/`
- compatible root: `.agents/skills/learntrace/`

OpenCode can also load a shared Skill directory through the `skills` array in
`opencode.json`. See <https://opencode.ai/docs/skills> for the current discovery
rules.

After installation, start OpenCode in the student project and ask it to use
`$learntrace`. OpenCode is required to generate a fresh OpenCode session export,
but not to process an existing authorized export or existing Task 3 result.
