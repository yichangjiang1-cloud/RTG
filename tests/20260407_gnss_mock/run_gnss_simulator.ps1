param(
    [string]$nmea_file = "tests\20260407_gnss_mock\COM34_km203_1_clean.log",
    [string]$target_host = "",
    [int]$port = 9001,
    [string]$receiver_id = "sim_receiver",
    [int]$rate = 10
)

$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Find-ProjectRoot {
    $current = (Get-Item .).FullName
    while ($current) {
        if (Test-Path (Join-Path $current "run_controller.ps1")) {
            return $current
        }
        $parent = Split-Path $current -Parent
        if ($parent -eq $current) { break }
        $current = $parent
    }
    return $null
}

$project_root = Find-ProjectRoot
if (-not $project_root) {
    Write-Host "ERROR: Cannot find project root" -ForegroundColor Red
    exit 1
}

Push-Location $project_root

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  GNSS Receiver Simulator" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv .venv
    Write-Host "Virtual environment created" -ForegroundColor Green
}

Write-Host "Activating virtual environment..." -ForegroundColor Gray
& .\.venv\Scripts\Activate.ps1

Write-Host "Checking dependencies..." -ForegroundColor Gray
pip install -q pyyaml 2>$null
Write-Host "Dependencies OK" -ForegroundColor Green

Write-Host ""
Write-Host "Configuration:" -ForegroundColor Cyan
Write-Host "  Project root: $project_root"
Write-Host "  NMEA file: $nmea_file"
Write-Host "  Receiver ID: $receiver_id"
if ($target_host) {
    Write-Host "  Target: $target_host`:$port"
} else {
    Write-Host "  Target: (自动获取本机IP):$port"
}
Write-Host "  Send rate: $rate lines/sec"
Write-Host ""

$env:PYTHONPATH = $project_root
if ($target_host) {
    $host_arg = @("--host", $target_host)
} else {
    $host_arg = @()
}
python "$PSScriptRoot\gnss_simulator.py" "$nmea_file" @host_arg --port $port --receiver-id $receiver_id --rate $rate

Pop-Location


