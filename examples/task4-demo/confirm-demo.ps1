$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$demo = Join-Path $root "examples\task4-demo"
$out = Join-Path $demo "out"
$snapshot = Join-Path $out "archive-records.json"

if (-not (Test-Path -LiteralPath $snapshot)) {
    throw "Run examples\task4-demo\run-demo.ps1 before confirming the demo."
}

Push-Location $root
try {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    $arguments = @(
        "-m", "learntrace.archive",
        $demo,
        "--snapshot", $snapshot,
        "--confirmations", (Join-Path $demo "student-confirmations.json.example"),
        "--output", (Join-Path $out "confirmed-learning-record.md"),
        "--records-output", (Join-Path $out "confirmed-archive-records.json")
    )

    if ($uv) {
        & $uv.Source run --locked python @arguments
    }
    elseif (Test-Path -LiteralPath $venvPython) {
        & $venvPython @arguments
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
