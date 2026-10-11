# Lets Donut (the Mac mini, 192.168.1.10) install and update software on this laptop over SSH.
# Run in PowerShell AS ADMINISTRATOR:  right-click Start > Terminal (Admin), then:
#   Set-ExecutionPolicy -Scope Process Bypass; & "$HOME\Downloads\setup-donut-ssh.ps1"
# What it does:
#   1. Turns on Windows' built-in OpenSSH Server.
#   2. Creates a separate admin account "donut" with a random password nobody knows - it can only log in with
#      the Mac's key, and only from the Mac's address.
#   3. Key-only logins, only the "donut" account, firewall open to the Mac only.
#   4. A small helper so Donut can run winget (Windows' package manager) over SSH.
# Undo everything: Remove-LocalUser donut; Stop-Service sshd; Set-Service sshd -StartupType Disabled
$ErrorActionPreference = "Stop"
$MAC = "192.168.1.10"

$key = (Read-Host "Paste the Mac's key (the line starting with ssh-ed25519)").Trim()
if ($key -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+') { throw "That doesn't look like the key - copy the whole line starting with ssh-ed25519." }

Write-Host "1/5 OpenSSH Server..."
if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State -ne 'Installed') {
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
}
Set-Service sshd -StartupType Automatic
Start-Service sshd
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null

Write-Host "2/5 'donut' account (admin, key-only)..."
if (-not (Get-LocalUser -Name donut -ErrorAction SilentlyContinue)) {
    Add-Type -AssemblyName System.Web
    $pw = ConvertTo-SecureString ([System.Web.Security.Membership]::GeneratePassword(32, 6)) -AsPlainText -Force
    New-LocalUser -Name donut -Password $pw -PasswordNeverExpires -Description "Donut agent - SSH key only" | Out-Null
}
Add-LocalGroupMember -Group Administrators -Member donut -ErrorAction SilentlyContinue

Write-Host "3/5 Mac's key (accepted only from $MAC)..."
$auth = "$env:ProgramData\ssh\administrators_authorized_keys"
$line = "from=`"$MAC`" $key"
if (-not (Test-Path $auth) -or -not (Select-String -Path $auth -SimpleMatch $key -Quiet)) { Add-Content -Path $auth -Value $line }
icacls $auth /inheritance:r /grant "*S-1-5-32-544:F" /grant "SYSTEM:F" | Out-Null

Write-Host "4/5 Locking down SSH..."
$cfg = "$env:ProgramData\ssh\sshd_config"
$c = Get-Content $cfg | Where-Object { $_ -notmatch '^(PasswordAuthentication|AllowUsers|PubkeyAuthentication)\b' }
$head = @("# Donut setup", "PubkeyAuthentication yes", "PasswordAuthentication no", "AllowUsers donut")
Set-Content -Path $cfg -Value ($head + $c)          # must sit above the 'Match Group administrators' block
Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue | Set-NetFirewallRule -RemoteAddress $MAC
Restart-Service sshd

Write-Host "5/5 winget helper..."
New-Item -ItemType Directory -Force "$env:ProgramData\donut" | Out-Null
@'
# Finds winget even for an account that never signs in on screen, then runs it.
$w = Get-ChildItem "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*__8wekyb3d8bbwe\winget.exe" -ErrorAction SilentlyContinue |
     Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $w) { Write-Output "winget not found - open Microsoft Store and update 'App Installer'."; exit 2 }
& $w.FullName @args
exit $LASTEXITCODE
'@ | Set-Content "$env:ProgramData\donut\winget.ps1"

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -like '192.168.1.*' } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "DONE. This laptop's address: $ip  -> reserve it in your router so it never changes."
Write-Host "Send Claude this address."
