#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$TaskName = "Pocket Entertainment Catalog",
    [switch]$RemoveFirewall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($RemoveFirewall) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "-RemoveFirewall needs an elevated PowerShell for the same Windows user. No changes were made."
    }
}
$Task = Get-ScheduledTask -TaskName $TaskName -TaskPath "\" -ErrorAction SilentlyContinue

if ($Task) {
    try {
        Stop-ScheduledTask -TaskName $TaskName -TaskPath "\"
        Unregister-ScheduledTask -TaskName $TaskName -TaskPath "\" -Confirm:$false
    }
    catch [System.UnauthorizedAccessException] {
        throw "Permission was denied. Re-run PowerShell as Administrator using the Windows account that owns this task."
    }
    Write-Host "Stopped and removed scheduled task: $TaskName"
}
else {
    Write-Host "Scheduled task was not found: $TaskName"
}
if ($RemoveFirewall) {
    Get-NetFirewallRule -DisplayName "Pocket Entertainment Catalog (Private LAN)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "Removed the Pocket Entertainment Catalog firewall rule."
}
else {
    Write-Host "Firewall was left as configured. To also remove its rules, run this script elevated with -RemoveFirewall."
}
