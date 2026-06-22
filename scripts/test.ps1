param(
    [ValidateSet("quick", "focus", "full", "postgres", "architecture", "docs", "slow", "e2e", "collect", "all")]
    [string]$Stage = "quick",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPythonCandidates = @(
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv/bin/python")
)
$Python = "python"
foreach ($Candidate in $VenvPythonCandidates) {
    if (Test-Path $Candidate) {
        $Python = $Candidate
        break
    }
}

$ArgsList = @("-m", "pytest")

switch ($Stage) {
    "focus" {
        $ArgsList += @(
            "-q",
            "-m",
            "not postgres and not slow and not docs and not local_artifact and not architecture"
        )
    }
    "quick" {
        $ArgsList += @(
            "-q",
            "-m",
            "not postgres and not slow and not docs and not local_artifact"
        )
    }
    "full" {
        $ArgsList += @("-q", "-m", "not postgres")
    }
    "postgres" {
        $ArgsList += @("-q", "-m", "postgres")
    }
    "architecture" {
        $ArgsList += @("-q", "-m", "architecture")
    }
    "docs" {
        $ArgsList += @("-q", "-m", "docs or local_artifact")
    }
    "slow" {
        $ArgsList += @("-q", "-m", "slow or concurrency")
    }
    "e2e" {
        $ArgsList += @("-q", "-m", "e2e")
    }
    "collect" {
        $ArgsList += @("--collect-only", "-q")
    }
    "all" {}
}

$ArgsList += $PytestArgs

Push-Location $ProjectRoot
$ExitCode = 0
try {
    & $Python @ArgsList
    $ExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($ExitCode -ne 0) {
    throw "pytest failed with exit code $ExitCode"
}
