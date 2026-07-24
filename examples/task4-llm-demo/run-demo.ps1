$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$demo = Join-Path $root "examples\task4-llm-demo"
$out = Join-Path $demo "out"
New-Item -ItemType Directory -Force -Path $out | Out-Null

if (-not $env:LEARNTRACE_LLM_API_KEY) {
    Write-Warning "LEARNTRACE_LLM_API_KEY is not set; learntrace will fall back to the deterministic stub."
}

& (Join-Path $root ".venv\Scripts\python.exe") -m learntrace.archive `
    $demo `
    --output (Join-Path $out "learning-record.md") `
    --records-output (Join-Path $out "archive-records.json") `
    --questions-output (Join-Path $out "learning-questions.md")

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
