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

## Make the Skill discoverable in OpenCode

Place the `learntrace` directory under one of OpenCode's supported Skill roots:

- project-local: `.opencode/skills/learntrace/`
- global: `~/.config/opencode/skills/learntrace/`
- compatible roots: `.agents/skills/learntrace/` or `.claude/skills/learntrace/`

OpenCode can also load a shared Skill directory through the `skills` array in
`opencode.json`. See <https://opencode.ai/docs/skills> for the current discovery
rules.

After installation, start OpenCode in the student project and ask it to use
`$learntrace`. OpenCode is required to generate a fresh OpenCode session export,
but not to process an existing authorized export or existing Task 3 result.
