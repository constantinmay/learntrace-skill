$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$demo = Join-Path $root "examples\task4-demo"
$out = Join-Path $demo "out"
New-Item -ItemType Directory -Force -Path $out | Out-Null

# LearnTrace is single-LLM: the CLI runs the deterministic local inferencer and
# never calls a remote model. No credentials or LEARNTRACE_* env vars are
# required (or read) here.
Push-Location $root
try {
    uv run --locked python -m learntrace.archive `
        $demo `
        --output (Join-Path $out "learning-record.md") `
        --records-output (Join-Path $out "archive-records.json") `
        --questions-output (Join-Path $out "learning-questions.md")
}
finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
