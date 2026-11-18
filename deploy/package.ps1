# 在开发机（Windows）上打中控发布包。
# 用法: powershell -File deploy\package.ps1
#       powershell -File deploy\package.ps1 -Version 1.0.1
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

if (-not $Version) {
    $Version = (Get-Content "deploy\VERSION" -Raw).Trim()
}
if (-not $Version) {
    throw "版本号为空。传入 -Version，或填写 deploy\VERSION"
}
if (-not (Test-Path "deploy\offline\wheels")) {
    throw "缺少 deploy\offline\wheels，离线包还没准备好"
}

$Name = "rtg-controller-$Version"
$Stage = Join-Path $Root "dist\$Name"
$Archive = Join-Path $Root "dist\$Name.tar.gz"

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path $Stage | Out-Null

Set-Content -Path (Join-Path $Stage "VERSION") -Value $Version -Encoding ascii -NoNewline
Copy-Item "requirements.txt" $Stage
Copy-Item "deploy\requirements-py38.txt" $Stage
Copy-Item "deploy\install.sh" $Stage
Copy-Item "deploy\rtg-controller.service" $Stage
Copy-Item "deploy\system.yaml.example" $Stage
Copy-Item "deploy\offline" (Join-Path $Stage "offline") -Recurse
Copy-Item "src" (Join-Path $Stage "src") -Recurse

Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem $Stage -Recurse -Include "*.pyc" -File | Remove-Item -Force

if (Test-Path $Archive) { Remove-Item $Archive -Force }
tar -czf $Archive -C (Join-Path $Root "dist") $Name

Write-Host "发布包: $Archive"
Write-Host "拷到 Ubuntu 的 /home/guide/CLionProjects/GNSS 后: tar -xzf $Name.tar.gz && cd $Name && sudo bash install.sh"
