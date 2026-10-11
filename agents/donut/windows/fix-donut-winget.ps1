# Fix: Windows blocks winget for an account connected over SSH. This makes Donut's helper run winget
# as the built-in SYSTEM account (the same way Intune/company IT tools do), then hand back the output.
# Run in PowerShell AS ADMINISTRATOR:
#   powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\fix-donut-winget.ps1"
$ErrorActionPreference = "Stop"
$D = "$env:ProgramData\donut"
New-Item -ItemType Directory -Force $D | Out-Null
# Only SYSTEM and Administrators may change files in this folder
icacls $D /inheritance:r /grant "*S-1-5-18:(OI)(CI)F" /grant "*S-1-5-32-544:(OI)(CI)F" | Out-Null

# 1. Runs AS SYSTEM inside a scheduled task: finds winget and runs it with the saved arguments.
@'
$D = "$env:ProgramData\donut"
$out = "$D\out.txt"
try {
    $w = Get-ChildItem "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe\winget.exe" -ErrorAction Stop |
         Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $w) { "winget not found - open Microsoft Store and update 'App Installer'." | Set-Content $out; exit 2 }
    $a = Get-Content "$D\args.json" -Raw | ConvertFrom-Json
    & $w.FullName @a *>&1 | Out-String -Width 200 | Set-Content $out -Encoding UTF8
    exit $LASTEXITCODE
} catch { "ERROR: $($_.Exception.Message)" | Set-Content $out; exit 1 }
'@ | Set-Content "$D\winget-system.ps1" -Encoding UTF8

# 2. What Donut calls over SSH (same name as before): saves the arguments, runs the task, prints the output.
@'
$D = "$env:ProgramData\donut"
$name = "DonutWinget"
if ((Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).State -eq "Running") {
    Write-Output "Another winget job is still running on the laptop - try again in a few minutes."; exit 3 }
ConvertTo-Json -InputObject @($args) -Compress | Set-Content "$D\args.json" -Encoding UTF8
Remove-Item "$D\out.txt" -ErrorAction SilentlyContinue
$act = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$D\winget-system.ps1`""
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $act -Settings $set -User "SYSTEM" -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 2
while ((Get-ScheduledTask -TaskName $name).State -eq "Running") { Start-Sleep -Seconds 3 }
$code = (Get-ScheduledTaskInfo -TaskName $name).LastTaskResult
Unregister-ScheduledTask -TaskName $name -Confirm:$false
if (Test-Path "$D\out.txt") { Get-Content "$D\out.txt" } else { Write-Output "(no output)" }
exit $code
'@ | Set-Content "$D\winget.ps1" -Encoding UTF8

Write-Host "Testing (this can take ~30 seconds the first time)..."
& powershell -NoProfile -ExecutionPolicy Bypass -File "$D\winget.ps1" --version
Write-Host ""
Write-Host "DONE. If you see a version like v1.x above, tell Donut to try again."
