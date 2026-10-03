# Registers a Task Scheduler task that starts scripts/price_agent_watchdog.ps1
# at logon for the current user (hidden window), so the debug Chrome and the
# shopee_th price agent come back by themselves after a reboot.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_price_agent_task.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_price_agent_task.ps1 -Uninstall

param([switch]$Uninstall)

$taskName = "ShopeeTH Price Agent Watchdog"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed task '$taskName'."
    exit 0
}

$watchdog = Join-Path $PSScriptRoot "price_agent_watchdog.ps1"
$user = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdog`"" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
# No execution time limit (the watchdog loops forever); restart it if it dies.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered task '$taskName' (runs at logon for $user)."
Write-Host "Start it now with: Start-ScheduledTask -TaskName '$taskName'"
