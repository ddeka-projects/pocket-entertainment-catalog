param(
    [ValidateSet("UserLogon", "SystemStartup")]
    [string]$Mode = "UserLogon"
)

$ErrorActionPreference = "Stop"

$TaskName = "Pocket Entertainment Catalog"
$Root = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot "run-server.ps1"
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentPrincipal = New-Object System.Security.Principal.WindowsPrincipal($CurrentIdentity)
$IsAdministrator = $CurrentPrincipal.IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not (Test-Path -LiteralPath $RunScript -PathType Leaf)) {
    throw "Could not find the server launcher: $RunScript"
}

if (-not (Test-Path -LiteralPath (Join-Path $Root "run.py") -PathType Leaf)) {
    throw "Could not find run.py under the project folder: $Root"
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $Root

if ($Mode -eq "SystemStartup") {
    if (-not $IsAdministrator) {
        throw "Run PowerShell as Administrator to install a pre-login SystemStartup task."
    }

    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal `
        -UserId "SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest
    $Description = "Runs Pocket Entertainment Catalog as SYSTEM when Windows starts."
}
else {
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentIdentity.Name
    $Principal = New-ScheduledTaskPrincipal `
        -UserId $CurrentIdentity.Name `
        -LogonType Interactive `
        -RunLevel Limited
    $Description = "Runs Pocket Entertainment Catalog when $($CurrentIdentity.Name) signs in."
}

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description $Description `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Mode: $Mode"
if ($Mode -eq "UserLogon") {
    Write-Host "The server will start whenever $($CurrentIdentity.Name) signs in."
    Write-Host "This mode preserves that user's Git identity and GitHub credentials."
}
else {
    Write-Warning "SYSTEM cannot normally use your personal Git identity or GitHub credentials."
    Write-Warning "Catalog saves will remain local if Git commit or push authentication is unavailable."
}
Write-Host "Log: $(Join-Path $Root '.tmp\server.log')"
