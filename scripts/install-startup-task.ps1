#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "Pocket Entertainment Catalog",
    [switch]$ConfigureFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$BackgroundRunner = Join-Path $PSScriptRoot "run-server-background.pyw"
$RuntimeHelpers = Join-Path $PSScriptRoot "python-runtime.ps1"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$firewallRuleName = "Pocket Entertainment Catalog (Private LAN)"
$CurrentPrincipal = New-Object System.Security.Principal.WindowsPrincipal($CurrentIdentity)
if ($ConfigureFirewall -and -not $CurrentPrincipal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "-ConfigureFirewall needs an elevated PowerShell for the same Windows user. Omit it if the rule is already configured."
}

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
if ($ConfigureFirewall) {
    $Python = Join-Path (Split-Path -Parent $Pythonw) "python.exe"
    Push-Location -LiteralPath $Root
    try {
        $configuredPort = & $Python -c "from pocket_entertainment_catalog.config import load_settings; print(load_settings().port)"
        if ($LASTEXITCODE -ne 0) { throw "Correct .env before configuring the firewall." }
    }
    finally {
        Pop-Location
    }
    $configuredPort = [int](($configuredPort | Select-Object -Last 1).Trim())
}
$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument "-B `"$BackgroundRunner`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentIdentity.Name
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentIdentity.Name `
    -LogonType Interactive `
    -RunLevel Limited
$Description = "Runs Pocket Entertainment Catalog when $($CurrentIdentity.Name) signs in."

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# Stop the previous instance, including an older SYSTEM registration, before
# replacing it so the newly configured account can bind the server port.
$Task = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -TaskPath "\"
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $Task = Get-ScheduledTask -TaskName $TaskName -TaskPath "\"
        if ($Task.State -notin @("Running", "Queued")) { break }
        if ((Get-Date) -ge $deadline) { throw "Timed out stopping '$TaskName'. No replacement server was started." }
        Start-Sleep -Milliseconds 250
    } while ($true)
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -TaskPath "\" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description $Description `
    -Force | Out-Null

if ($ConfigureFirewall) {
    Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $configuredPort -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

Start-ScheduledTask -TaskName $TaskName -TaskPath "\"

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Background Python: $Pythonw"
Write-Host "The server will start whenever $($CurrentIdentity.Name) signs in, with limited privileges."
Write-Host "This preserves that user's Git identity and GitHub credentials."
Write-Host "Log: $(Join-Path $Root '.tmp\server.log')"
if ($ConfigureFirewall) {
    Write-Host "Allowed TCP port $configuredPort only from the local subnet on Private networks."
}
else {
    Write-Host "Firewall was left as configured. To add Private/LocalSubnet rules, run this script elevated with -ConfigureFirewall."
}
