function Get-PocketCatalogDotEnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$DefaultValue
    )

    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        return $DefaultValue
    }

    $escapedName = [regex]::Escape($Name)
    $line = Get-Content -LiteralPath $envFile |
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

function Resolve-PocketCatalogPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $configured = Get-PocketCatalogDotEnvValue `
        -Root $Root `
        -Name "POCKET_CATALOG_PYTHON" `
        -DefaultValue ""

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

function Resolve-PocketCatalogPythonw {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $python = Resolve-PocketCatalogPython -Root $Root
    $consoleExecutable = $python.Executable
    $consoleName = [IO.Path]::GetFileName($consoleExecutable)

    if ($consoleName -ieq "pythonw.exe") {
        return $consoleExecutable
    }

    if ($consoleName -ieq "py.exe") {
        $resolveArguments = @($python.PrefixArguments)
        $resolveArguments += @("-c", "import sys; print(sys.executable)")
        $resolvedLines = @(& $consoleExecutable @resolveArguments)
        if ($LASTEXITCODE -ne 0 -or $resolvedLines.Count -eq 0) {
            throw "The Python launcher could not resolve the Python interpreter."
        }
        $consoleExecutable = $resolvedLines[-1].Trim()
    }

    $pythonw = Join-Path (Split-Path -Parent $consoleExecutable) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        throw "Could not find pythonw.exe beside the configured Python interpreter: $consoleExecutable"
    }

    return (Resolve-Path -LiteralPath $pythonw).Path
}
