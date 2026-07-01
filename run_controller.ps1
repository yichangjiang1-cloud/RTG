# RTG 中控机程序启动脚本
# 使用方法：.\run_controller.ps1

# 设置输出编码为 UTF-8，避免中文乱码
$OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  RTG 高精度定位系统 - 中控机程序" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
# 检查虚拟环境
if (-not (Test-Path ".venv")) {
    Write-Host "⚠ 未检测到虚拟环境，正在创建..." -ForegroundColor Yellow
    python -m venv .venv
    Write-Host "✓ 虚拟环境创建完成" -ForegroundColor Green
}
# 激活虚拟环境
Write-Host "正在激活虚拟环境..." -ForegroundColor Gray
& .\.venv\Scripts\Activate.ps1
# 安装依赖（简化版，直接安装）
Write-Host "检查并安装依赖包..." -ForegroundColor Gray
pip install -q -r requirements.txt
Write-Host "✓ 依赖包检查完成" -ForegroundColor Green
# 创建必要的目录
Write-Host "检查目录结构..." -ForegroundColor Gray
$directories = @("logs", "data")
foreach ($dir in $directories) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
        Write-Host "✓ 创建目录: $dir" -ForegroundColor Green
    }
}
# 启动程序
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  正在启动中控机程序..." -ForegroundColor Cyan
Write-Host "  按 Ctrl+C 停止程序" -ForegroundColor Yellow
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
python -m src.main
