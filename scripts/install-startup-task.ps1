param()

$ErrorActionPreference = "Stop"

$TaskName = "Pocket Entertainment Catalog"
$Root = Split-Path -Parent $PSScriptRoot
$BackgroundRunner = Join-Path $PSScriptRoot "run-server-background.pyw"
$RuntimeHelpers = Join-Path $PSScriptRoot "python-runtime.ps1"
$CurrentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent()

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

Start-ScheduledTask -TaskName $TaskName -TaskPath "\"

Write-Host "Installed and started scheduled task: $TaskName"
Write-Host "Background Python: $Pythonw"
Write-Host "The server will start whenever $($CurrentIdentity.Name) signs in, with limited privileges."
Write-Host "This preserves that user's Git identity and GitHub credentials."
Write-Host "Log: $(Join-Path $Root '.tmp\server.log')"
