param()

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Root "run.py"
$LogDir = Join-Path $Root ".tmp"
$LogFile = Join-Path $LogDir "server.log"
$RuntimeHelpers = Join-Path $PSScriptRoot "python-runtime.ps1"

if (-not (Test-Path -LiteralPath $RuntimeHelpers -PathType Leaf)) {
    throw "Could not find the Python runtime helpers: $RuntimeHelpers"
}
. $RuntimeHelpers

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Could not find the catalog runner: $Runner"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONUNBUFFERED = "1"

Push-Location $Root
try {
    Start-Transcript -Path $LogFile -Append | Out-Null
    $python = Resolve-PocketCatalogPython -Root $Root
    $pythonExecutable = $python.Executable
    $pythonArguments = @($python.PrefixArguments)

    $versionArguments = @($pythonArguments)
    $versionArguments += @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    )
    & $pythonExecutable @versionArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Pocket Entertainment Catalog requires Python 3.10 or newer."
    }

    $runArguments = @($pythonArguments)
    $runArguments += @("-B", $Runner)
    Write-Host "Starting Pocket Entertainment Catalog with $pythonExecutable"
    & $pythonExecutable @runArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Pocket Entertainment Catalog exited with code $LASTEXITCODE."
    }
}
finally {
    try {
        Stop-Transcript | Out-Null
    }
    catch {
    }
    Pop-Location
}
