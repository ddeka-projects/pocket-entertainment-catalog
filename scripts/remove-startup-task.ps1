param()

$ErrorActionPreference = "Stop"

$TaskName = "Pocket Entertainment Catalog"
$Task = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "Scheduled task was not found: $TaskName"
    return
}

try {
    if ($Task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath "\"
    }

    Unregister-ScheduledTask -TaskName $TaskName -TaskPath "\" -Confirm:$false
}
catch [System.UnauthorizedAccessException] {
    throw "Permission was denied. Re-run PowerShell as Administrator using the Windows account that owns this task."
}

Write-Host "Stopped and removed scheduled task: $TaskName"
