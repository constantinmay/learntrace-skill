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
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if ($uv) {
        & $uv.Source run --locked python -m learntrace.archive `
            $demo `
            --output (Join-Path $out "learning-record.md") `
            --records-output (Join-Path $out "archive-records.json") `
            --questions-output (Join-Path $out "learning-questions.md")
    }
    elseif (Test-Path -LiteralPath $venvPython) {
        & $venvPython -m learntrace.archive `
            $demo `
            --output (Join-Path $out "learning-record.md") `
            --records-output (Join-Path $out "archive-records.json") `
            --questions-output (Join-Path $out "learning-questions.md")
    }
    else {
        throw "Neither uv nor the project .venv Python is available."
    }
}
finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
