# Registers an hourly Windows scheduled task that runs the Pocket Casts logger.
# Run from PowerShell in this folder:   powershell -ExecutionPolicy Bypass -File .\setup_task.ps1
# Optional: -NtfyTopic "some-hard-to-guess-name" to get phone alerts when it keeps failing.

param(
    [string]$NtfyTopic = ""
)

$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "pc_history.py"

# pythonw.exe runs without flashing a console window every hour.
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $py = (Get-Command python.exe -ErrorAction Stop).Source
    $pythonw = Join-Path (Split-Path $py) "pythonw.exe"
}
if (-not (Test-Path $pythonw)) { throw "Could not find pythonw.exe. Is Python installed and on PATH?" }

if ($NtfyTopic) {
    [Environment]::SetEnvironmentVariable("NTFY_TOPIC", $NtfyTopic, "User")
    Write-Host "NTFY_TOPIC set for your user account."
}

$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(7) `
           -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
            -MultipleInstances IgnoreNew
# Runs as you, only while you're signed in -- needed so it can read your
# saved password from Windows Credential Manager.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

Register-ScheduledTask -TaskName "Pocket Casts History Logger" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "Scheduled task 'Pocket Casts History Logger' registered (hourly, at :07)."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName 'Pocket Casts History Logger'"
