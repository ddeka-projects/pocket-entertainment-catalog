param(
    [ValidateSet("UserLogon", "SystemStartup")]
    [string]$Mode = "UserLogon"
)

$ErrorActionPreference = "Stop"

$TaskName = "Pocket Entertainment Catalog"
$Root = Split-Path -Parent $PSScriptRoot
$BackgroundRunner = Join-Path $PSScriptRoot "run-server-background.pyw"
$RuntimeHelpers = Join-Path $PSScriptRoot "python-runtime.ps1"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentPrincipal = New-Object System.Security.Principal.WindowsPrincipal($CurrentIdentity)
$IsAdministrator = $CurrentPrincipal.IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not (Test-Path -LiteralPath $BackgroundRunner -PathType Leaf)) {
    throw "Could not find the background server launcher: $BackgroundRunner"
}

if (-not (Test-Path -LiteralPath $RuntimeHelpers -PathType Leaf)) {
    throw "Could not find the Python runtime helpers: $RuntimeHelpers"
}
. $RuntimeHelpers

if (-not (Test-Path -LiteralPath (Join-Path $Root "run.py") -PathType Leaf)) {
    throw "Could not find run.py under the project folder: $Root"
}

$Pythonw = Resolve-PocketCatalogPythonw -Root $Root
$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "-B `"$BackgroundRunner`"" `
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
Write-Host "Background Python: $Pythonw"
if ($Mode -eq "UserLogon") {
    Write-Host "The server will start whenever $($CurrentIdentity.Name) signs in."
    Write-Host "This mode preserves that user's Git identity and GitHub credentials."
}
else {
    Write-Warning "SYSTEM cannot normally use your personal Git identity or GitHub credentials."
    Write-Warning "Catalog saves will remain local if Git commit or push authentication is unavailable."
}
Write-Host "Log: $(Join-Path $Root '.tmp\server.log')"
