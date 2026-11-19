# Register a Windows Task Scheduler entry that starts AutoContent AI (hidden) at logon.
# Usage:   powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
# Remove:  powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1 -Unregister
param([switch]$Unregister)
$name = "AutoContent AI"
if ($Unregister) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    Write-Host "Removed task '$name'"
    exit 0
}
$root = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\start.ps1`" -Hidden"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts the AutoContent AI local server at logon" -Force | Out-Null
Write-Host "Registered '$name' (runs at logon for $env:USERNAME)."
