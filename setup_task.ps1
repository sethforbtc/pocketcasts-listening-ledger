# Registers the Listening Ledger's Windows scheduled tasks.
# Run from PowerShell in this folder:
#   powershell -ExecutionPolicy Bypass -File .\setup_task.ps1
#
# Options:
#   -NtfyTopic "your-topic"   save an ntfy topic for failure alerts (see docs/ntfy.md)
#   -NoDashboard              don't start the dashboard automatically at sign-in
#   -Port 8770                dashboard port

param(
    [string]$NtfyTopic = "",
    [switch]$NoDashboard,
    [int]$Port = 8770
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# pythonw.exe runs without flashing a console window.
$python  = (Get-Command python.exe -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { throw "Could not find pythonw.exe next to $python" }

$user = "$env:USERDOMAIN\$env:USERNAME"
# Runs as you, only while you're signed in -- needed to read your saved
# password from Windows Credential Manager.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive

# 1) Hourly collector
$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$here\pc_history.py`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(7) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Pocket Casts History Logger" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Registered 'Pocket Casts History Logger' (hourly, at :07)."

# 2) Dashboard, started at sign-in and kept running
if (-not $NoDashboard) {
    $dAction  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$here\dashboard.py`" --no-browser --port $Port" -WorkingDirectory $here
    $dTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $dSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
                 -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
                 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "Pocket Casts Ledger Dashboard" -Action $dAction -Trigger $dTrigger `
        -Settings $dSettings -Principal $principal -Force | Out-Null
    Start-ScheduledTask -TaskName "Pocket Casts Ledger Dashboard"
    Write-Host "Registered 'Pocket Casts Ledger Dashboard' (starts at sign-in). Open http://127.0.0.1:$Port"
}

if ($NtfyTopic) {
    & $python "$here\pc_history.py" --set-ntfy $NtfyTopic --test-notify
}

Write-Host "Run the collector now with:  Start-ScheduledTask -TaskName 'Pocket Casts History Logger'"
