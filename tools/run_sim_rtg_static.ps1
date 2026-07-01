# 双接收机四天线静止 GNSS 模拟（9001 + 9002）
# 用法：在项目根目录执行  .\tools\run_sim_rtg_static.ps1

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python tools/sim_rtg_gnss_static.py --use-yard-origin @args
