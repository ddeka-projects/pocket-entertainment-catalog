param()

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $Root "run.py"
$EnvFile = Join-Path $Root ".env"
$LogDir = Join-Path $Root ".tmp"
$LogFile = Join-Path $LogDir "server.log"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$DefaultValue
    )

    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        return $DefaultValue
    }

    $escapedName = [regex]::Escape($Name)
    $line = Get-Content -LiteralPath $EnvFile |
        Where-Object { $_ -match "^\s*$escapedName\s*=" } |
        Select-Object -Last 1

    if (-not $line) {
        return $DefaultValue
    }

    $value = ($line -split "=", 2)[1].Trim()
    if ($value.Length -ge 2) {
        $first = $value.Substring(0, 1)
        $last = $value.Substring($value.Length - 1, 1)
        if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
    }

    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }

    return $value
}

function Resolve-Python {
    $configured = Get-DotEnvValue -Name "POCKET_CATALOG_PYTHON" -DefaultValue ""
    if (-not [string]::IsNullOrWhiteSpace($configured)) {
        $expanded = [Environment]::ExpandEnvironmentVariables($configured)
        $paths = @($expanded)
        if (-not [IO.Path]::IsPathRooted($expanded)) {
            $paths += Join-Path $Root $expanded
        }

        foreach ($path in $paths) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $prefix = @()
                if ([IO.Path]::GetFileName($path) -ieq "py.exe") {
                    $prefix = @("-3")
                }
                return [PSCustomObject]@{
                    Executable = (Resolve-Path -LiteralPath $path).Path
                    PrefixArguments = $prefix
                }
            }
        }

        $configuredCommand = Get-Command -Name $expanded -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($configuredCommand) {
            $prefix = @()
            if ($configuredCommand.Name -ieq "py.exe") {
                $prefix = @("-3")
            }
            return [PSCustomObject]@{
                Executable = $configuredCommand.Source
                PrefixArguments = $prefix
            }
        }

        throw "POCKET_CATALOG_PYTHON does not resolve to a Python executable: $configured"
    }

    foreach ($candidate in @("py.exe", "python.exe", "python3.exe")) {
        $command = Get-Command -Name $candidate -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($command) {
            $prefix = @()
            if ($candidate -ieq "py.exe") {
                $prefix = @("-3")
            }
            return [PSCustomObject]@{
                Executable = $command.Source
                PrefixArguments = $prefix
            }
        }
    }

    throw "Python 3 was not found. Install Python 3.10 or newer, or set POCKET_CATALOG_PYTHON in .env."
}

if (-not (Test-Path -LiteralPath $Runner -PathType Leaf)) {
    throw "Could not find the catalog runner: $Runner"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONUNBUFFERED = "1"

Push-Location $Root
try {
    Start-Transcript -Path $LogFile -Append | Out-Null
    $python = Resolve-Python
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
