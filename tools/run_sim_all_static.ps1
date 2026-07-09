# Gantry moving + trolley rigid on gantry (9001/9002/9003)
# Usage (from project root):  .\tools\run_sim_all_static.ps1

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}
if (-not (Test-Path $Py)) {
    Write-Host "Python not found." -ForegroundColor Red
    exit 1
}

$Script = Join-Path $Root "tools\sim_rtg_trolley_gnss.py"
if (-not (Test-Path $Script)) {
    Write-Host "Missing: $Script" -ForegroundColor Red
    exit 1
}

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  GNSS sim: gantry MOVING + trolley on gantry" -ForegroundColor Cyan
Write-Host "  Single window: 9001 + 9002 + 9003" -ForegroundColor Cyan
Write-Host "  Controller must be running (run_controller.ps1)" -ForegroundColor Yellow
Write-Host "================================================" -ForegroundColor Cyan

$simArgs = @($Script, "--use-yard-origin") + @($args)

Start-Process -FilePath $Py `
    -ArgumentList $simArgs `
    -WorkingDirectory $Root `
    -WindowStyle Normal

Write-Host "Started. Gantry moves along yard heading; trolley stays on beam." -ForegroundColor Green
