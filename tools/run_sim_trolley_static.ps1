# 小车双 GGA 静止 GNSS 模拟（UDP 9003）
# 用法：在项目根目录执行  .\tools\run_sim_trolley_static.ps1
# 联调：需同时有大车数据 → 另开窗口运行 .\tools\run_sim_rtg_static.ps1
#       或一键  .\tools\run_sim_all_static.ps1
# 若中控已长期运行，修改 UDP 绑定后请先重启 run_controller.ps1

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python tools/sim_trolley_gnss_static.py --use-yard-origin @args