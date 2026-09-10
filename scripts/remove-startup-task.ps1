param()

$ErrorActionPreference = "Stop"

$TaskName = "Pocket Entertainment Catalog"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if (-not $Task) {
    Write-Host "Scheduled task was not found: $TaskName"
    return
}

try {
    if ($Task.State -eq "Running") {
        Stop-ScheduledTask -TaskName $TaskName
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
catch [System.UnauthorizedAccessException] {
    throw "Permission was denied. Re-run PowerShell as Administrator to remove a SystemStartup task."
}

Write-Host "Stopped and removed scheduled task: $TaskName"
