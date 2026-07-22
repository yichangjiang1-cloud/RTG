# ============================================================
# Run Mosquitto as a Windows Scheduled Task (auto-start at boot + restart on crash)
# RTG project - Windows deployment.
#
# Why a Scheduled Task instead of a service:
#   mosquitto 2.1.2's native Windows service mode ('mosquitto install' -> "run")
#   silently exits (code 0) on this machine and leaves no log/event/process.
#   Its FOREGROUND mode (mosquitto.exe -c <conf>) works perfectly, so we run
#   that proven mode headless via Task Scheduler, which handles boot-start and
#   auto-restart for us. No external tools required.
#
# Usage: run in an ELEVATED (Administrator) PowerShell:
#   .\install-mosquitto-service.ps1
#
# Optional parameters:
#   -MosquittoDir "D:\tools\Mosquitto"   folder containing mosquitto.exe
#   -RuntimeDir   "D:\mosquitto"         root for data/log/passwd/conf
# ============================================================

param(
    [string]$MosquittoDir = "D:\tools\Mosquitto",
    [string]$RuntimeDir   = "D:\mosquitto",
    [string]$ConfPath     = "",
    [string]$TaskName     = "Mosquitto MQTT Broker"
)

if ([string]::IsNullOrWhiteSpace($ConfPath)) {
    $ConfPath = Join-Path $RuntimeDir "mosquitto.conf"
}

$ErrorActionPreference = "Stop"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p  = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Please run this script from an ELEVATED (Administrator) PowerShell."
    }
}

Assert-Admin

$exe = Join-Path $MosquittoDir "mosquitto.exe"
if (-not (Test-Path $exe)) {
    throw "mosquitto.exe not found at: $exe . Use -MosquittoDir to point to the install folder."
}

# 1) Prepare data/log directories
$dataDir = Join-Path $RuntimeDir "data"
$logDir  = Join-Path $RuntimeDir "log"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir  | Out-Null
Write-Host "[OK] data dir: $dataDir" -ForegroundColor Green
Write-Host "[OK] log  dir: $logDir"  -ForegroundColor Green

# 1b) Reset ownership/permissions on the runtime dir. Earlier service attempts
#     created SYSTEM-owned files (e.g. data\mosquitto.db) that even an admin
#     can't modify. Take ownership, grant the current user + SYSTEM full control,
#     then drop stale db/log so fresh ones are created by the account we use.
takeown /F $RuntimeDir /R /D Y | Out-Null
icacls $RuntimeDir /grant "$($env:USERNAME):(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" /T /C | Out-Null
Remove-Item (Join-Path $dataDir "*.db") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $logDir  "*.log") -Force -ErrorAction SilentlyContinue
Write-Host "[OK] reset permissions on $RuntimeDir and cleared stale db/log" -ForegroundColor Green

# 2) Deploy the project config to the runtime location
$srcConf = Join-Path $PSScriptRoot "mosquitto.win.conf"
if (-not (Test-Path $srcConf)) {
    throw "Source config not found: $srcConf"
}
New-Item -ItemType Directory -Force -Path (Split-Path $ConfPath) | Out-Null
Copy-Item $srcConf $ConfPath -Force
Write-Host "[OK] config deployed: $ConfPath" -ForegroundColor Green

# 3) Warn if password file is missing
$passwd = Join-Path $RuntimeDir "passwd"
if (-not (Test-Path $passwd)) {
    Write-Host "[WARN] password file not found: $passwd" -ForegroundColor Yellow
    Write-Host "       Config has 'allow_anonymous false'; broker will reject clients until you create a user:" -ForegroundColor Yellow
    Write-Host "       & '$MosquittoDir\mosquitto_passwd.exe' -c '$passwd' rtg_user" -ForegroundColor Yellow
}

# 4) Validate the config before wiring anything up
& $exe -c $ConfPath --test-config
if ($LASTEXITCODE -ne 0) {
    throw "mosquitto config test failed for $ConfPath"
}
Write-Host "[OK] config test passed" -ForegroundColor Green

# 5) Remove any leftover native service from earlier attempts
$svc = Get-Service -Name "mosquitto" -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "[..] removing leftover native 'mosquitto' service" -ForegroundColor Yellow
    sc.exe stop mosquitto 2>$null | Out-Null
    Start-Sleep -Seconds 1
    & $exe uninstall 2>$null
    sc.exe delete mosquitto 2>$null | Out-Null
    Start-Sleep -Seconds 1
}

# 6) Remove existing scheduled task if present
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "[..] existing scheduled task found; recreating" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 7) Create the scheduled task: run mosquitto (foreground mode) as the CURRENT
#    user with S4U logon (runs in background, no stored password, no console
#    attachment). This avoids two classes of problems:
#    - SYSTEM account: lacks permission to D:\ data drive files
#    - Interactive logon: process gets killed when any console window closes
#      (exit code 0xC000013A = STATUS_CONTROL_C_EXIT)
#    S4U = "run whether user is logged on or not" without storing a password.
#    The process runs in session 0 (no desktop), invisible and persistent.
$userId = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute $exe -Argument "-c `"$ConfPath`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "RTG project MQTT broker (mosquitto foreground via Task Scheduler)" | Out-Null
Write-Host "[OK] scheduled task '$TaskName' created (AtStartup, S4U $userId, auto-restart)" -ForegroundColor Green

# 8) Open firewall for TCP 1883 (needed when the controller connects remotely)
if (-not (Get-NetFirewallRule -DisplayName "Mosquitto MQTT 1883" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Mosquitto MQTT 1883" -Direction Inbound `
        -Protocol TCP -LocalPort 1883 -Action Allow | Out-Null
    Write-Host "[OK] firewall rule added for TCP 1883" -ForegroundColor Green
} else {
    Write-Host "[SKIP] firewall rule for TCP 1883 already exists" -ForegroundColor Yellow
}

# 9) Start it now and verify
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$listening = Get-NetTCPConnection -LocalPort 1883 -State Listen -ErrorAction SilentlyContinue
$proc = Get-Process mosquitto -ErrorAction SilentlyContinue
if ($proc -and $listening) {
    Write-Host "[OK] mosquitto is running (PID $($proc.Id)) and listening on 1883." -ForegroundColor Green
} else {
    Write-Host "[WARN] not confirmed running yet. Recent log:" -ForegroundColor Red
    if (Test-Path "$logDir\mosquitto.log") { Get-Content "$logDir\mosquitto.log" -Tail 20 }
}

Write-Host ""
Write-Host "Done. Management commands:" -ForegroundColor Cyan
Write-Host "  status : Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "  stop   : Stop-ScheduledTask -TaskName '$TaskName'"
Write-Host "  start  : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  logs   : Get-Content $logDir\mosquitto.log -Tail 50 -Wait"
