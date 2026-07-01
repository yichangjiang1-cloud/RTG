"""
多接收机模拟启动脚本
同时运行 3 个 GNSS 接收机模拟器：
  - receiver_a1: COM6_km301_1_clean.log
  - receiver_a2: COM10_mc282_qx_1_clean.log  
  - receiver_trolley: COM34_km203_1_clean.log
"""

import subprocess
import sys
import time
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
TEST_DATA_DIR = PROJECT_ROOT / "tests" / "20260407_gnss_mock"
GNSS_SIMULATOR = Path(__file__).parent / "gnss_simulator.py"

# 定义三个接收机的配置（端口与 config/system.yaml gnss.* 一致）
RECEIVERS = [
    {
        "nmea_file": TEST_DATA_DIR / "COM6_km301_1_clean.log",
        "receiver_id": "gantry_a1",
        "port": 9001,
        "description": "大车 A1（电气房侧）",
    },
    {
        "nmea_file": TEST_DATA_DIR / "COM10_mc282_qx_1_clean.log",
        "receiver_id": "gantry_a2",
        "port": 9002,
        "description": "大车 A2（柴油机侧）",
    },
    {
        "nmea_file": TEST_DATA_DIR / "COM34_km203_1_clean.log",
        "receiver_id": "trolley",
        "port": 9003,
        "description": "小车",
    },
]

def main():
    print("=" * 70)
    print("  GNSS 接收机模拟器 - 多接收机启动")
    print("=" * 70)
    print()
    
    # 检查文件
    for rcv in RECEIVERS:
        if not rcv["nmea_file"].exists():
            print(f"✗ 错误: NMEA 文件不存在: {rcv['nmea_file']}")
            return 1
    
    print("✓ 所有 NMEA 文件检查完成\n")
    
    # 启动所有接收机
    processes = []
    
    for rcv in RECEIVERS:
        cmd = [
            sys.executable,
            str(GNSS_SIMULATOR),
            str(rcv["nmea_file"]),
            "--port", str(rcv["port"]),
            "--receiver-id", rcv["receiver_id"],
            "--rate", "10",
        ]
        
        print(f"启动 {rcv['description']} ({rcv['receiver_id']})...")
        print(f"  NMEA 文件: {rcv['nmea_file'].name}")
        print(f"  端口: {rcv['port']}")
        print()
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            processes.append(proc)
        except Exception as e:
            print(f"✗ 启动失败: {e}")
            return 1
    
    print("=" * 70)
    print(f"✓ 已启动 {len(processes)} 个接收机模拟器")
    print("  接收机数据推送情况：")
    for rcv in RECEIVERS:
        print(f"    - {rcv['description']}: UDP {rcv['port']}")
    print("  按 Ctrl+C 停止所有模拟器")
    print("=" * 70)
    print()
    
    # 保持运行
    try:
        while True:
            time.sleep(1)
            # 检查进程是否还活着
            for i, proc in enumerate(processes):
                if proc.poll() is not None:
                    print(f"⚠ 接收机 {i} 已退出")
    except KeyboardInterrupt:
        print("\n收到中断信号，停止所有模拟器...")
        for proc in processes:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except:
                proc.kill()
        print("✓ 所有模拟器已停止")

if __name__ == "__main__":
    sys.exit(main())

