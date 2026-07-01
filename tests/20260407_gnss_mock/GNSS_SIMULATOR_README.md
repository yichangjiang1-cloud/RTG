# GNSS 接收机模拟器使用指南

## 📋 概述

GNSS 接收机模拟器从 NMEA 日志文件读取真实采集的 GPS 数据，通过 UDP 推送到中控机，用于测试和验证定位算法。

日志文件通常同时包含 **GGA（定位主天线）** 与 **PASHR/GPTRA 等姿态语句（双天线航向）**，与现场双天线接收机行为一致。

## 🔌 与中控 `config/system.yaml` 端口对应

整机 **3 机 6 天线**，模拟器按 **3 路 UDP** 推送（非 6 路）：

| 模拟端口 | `system.yaml` role | 日志文件 | 说明 |
|----------|-------------------|----------|------|
| **9001** | `gantry_a1` | `COM6_km301_1_clean.log` | 大车 A1（电气房侧），双天线接收机 |
| **9002** | `gantry_a2` | `COM10_mc282_qx_1_clean.log` | 大车 A2（柴油机侧），双天线接收机 |
| **9003** | `trolley` | `COM34_km203_1_clean.log` | 小车，双天线接收机 |

一键启动三路：`python tests/20260407_gnss_mock/run_gnss_simulators.py`

## 📊 支持的 NMEA 日志文件

项目中包含 4 个真实采集的 NMEA 日志文件：

| 文件 | GPS 模块 | 说明 |
|------|---------|------|
| `COM6_km301_1_clean.log` | KM301 | 门机 A1 数据 |
| `COM10_mc282_qx_1_clean.log` | MC282/QX | 门机 A2 数据 |
| `COM34_km203_1_clean.log` | KM203 | 小车数据 |
| `COM20_lc29h_1_clean.log` | LC29H | 备选数据 |

## 🚀 快速开始

### 方式1：PowerShell 启动脚本（推荐 Windows）

**启动单个接收机**
```powershell
.\run_gnss_simulator.ps1
```

**自定义参数**
```powershell
.\run_gnss_simulator.ps1 `
    -nmea_file "tests\20260407\COM10_mc282_qx_1_clean.log" `
    -receiver_id "receiver_a2" `
    -port 9001 `
    -rate 10
```

### 方式2：直接运行 Python 脚本

**单个接收机**
```bash
python tools/gnss_simulator.py tests/20260407_gnss_mock/COM34_km203_1_clean.log \
    --host 127.0.0.1 \
    --port 9001 \
    --receiver-id receiver_trolley \
    --rate 10
```

**Linux/Ubuntu**
```bash
python3 tools/gnss_simulator.py tests/20260407_gnss_mock/COM34_km203_1_clean.log \
    --host 127.0.0.1 \
    --port 9001
```

### 方式3：Python 多接收机启动脚本（推荐联调）

同时启动 3 个接收机模拟器，端口与 `config/system.yaml` 一致（9001/9002/9003）：

```bash
python tests/20260407_gnss_mock/run_gnss_simulators.py
```

## 📝 命令行参数

| 参数 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `nmea_file` | NMEA 日志文件路径（必填） | - | `tests/20260407/COM34_km203_1_clean.log` |
| `--host` | 目标接收地址 | `127.0.0.1` | `192.168.1.100` |
| `--port` | 目标 UDP 端口 | `9001` | `9001`, `9002` |
| `--receiver-id` | 接收机标识符 | `sim_receiver` | `receiver_a1`, `receiver_trolley` |
| `--rate` | 发送速率（行/秒） | `10` | `5`, `20` |

## 🔄 工作流程

```
NMEA 日志文件
     │
     ▼
读取并缓存所有 NMEA 语句
     │
     ▼
按照指定速率循环发送
     │
     ├─> UDP :9001 (接收机数据)
     │
     ▼
中控机 GnssReceiver 监听并处理
     │
     ▼
发布到 EventBus
     │
     ▼
FusionNode 接收和融合
```

## 💡 使用场景

### 场景1：单个接收机测试
```powershell
.\run_gnss_simulator.ps1 -nmea_file "tests\20260407\COM6_km301_1_clean.log"
```

### 场景2：多接收机同时测试
在 3 个不同的终端中运行：

**终端 1 - 门机 A1**
```powershell
.\run_gnss_simulator.ps1 -nmea_file "tests\20260407\COM6_km301_1_clean.log" -receiver_id "receiver_a1"
```

**终端 2 - 门机 A2**
```powershell
.\run_gnss_simulator.ps1 -nmea_file "tests\20260407\COM10_mc282_qx_1_clean.log" -receiver_id "receiver_a2"
```

**终端 3 - 小车**
```powershell
.\run_gnss_simulator.ps1 -nmea_file "tests\20260407\COM34_km203_1_clean.log" -receiver_id "receiver_trolley"
```

**同时启动中控机**
```powershell
.\run_controller.ps1
```

### 场景3：远程测试
在远程设备上运行模拟器，向中控机推送数据：
```powershell
.\run_gnss_simulator.ps1 -host 192.168.1.100 -port 9001
```

### 场景4：低速率测试（调试）
```powershell
.\run_gnss_simulator.ps1 -rate 2  # 每秒 2 行
```

## 📊 输出日志示例

```
(.venv) PS D:\kpl\pycharmproject\RTG> .\run_gnss_simulator.ps1

================================================
  GNSS 接收机模拟器
================================================

正在激活虚拟环境...
检查依赖包...
✓ 依赖包检查完成

模拟器配置:
  NMEA 文件: tests\20260407\COM34_km203_1_clean.log
  接收机 ID: sim_receiver
  目标地址: 127.0.0.1:9001
  发送速率: 10 行/秒

============================================================
  GNSS 模拟器启动
  接收机 ID: sim_receiver
  目标地址: 127.0.0.1:9001
  发送速率: 10 行/秒 (间隔 100ms)
  NMEA 语句数: 3245
  预计循环时间: 5.4 分钟
  按 Ctrl+C 停止模拟器
============================================================
✓ 加载 NMEA 文件: tests\20260407\COM34_km203_1_clean.log
  总行数: 3245
已发送 100 行数据
↻ NMEA 数据循环播放（已发送 3245 行）
实时速率: 10.0 行/秒 | 总已发送: 3345 | 错误: 0
```

## 🔧 常见问题

### Q: 连接被拒绝错误
```
✗ 发送失败: [WinError 10049] The requested address is not valid in its context
```
**解决方案：** 检查目标地址是否正确，如果是远程机器需要正确的 IP 地址。

### Q: 模拟器在循环播放数据，怎样让它只播放一遍？
修改 `gnss_simulator.py` 中的 `_run_loop()` 方法，将循环复位改为 `break`：
```python
    def _run_loop(self) -> None:
        line_index = 0
        while self._running:
            nmea_line = self.nmea_lines[line_index]
            if self.send_nmea(nmea_line):
                ...
            # 移动到下一行
            line_index += 1
            if line_index >= len(self.nmea_lines):
                # line_index = 0  # 注释此行禁用循环
                break  # 播放完成后退出
```

### Q: 能否改变发送速率？
可以。使用 `--rate` 参数，单位是行/秒：
```powershell
# 每秒 5 行
.\run_gnss_simulator.ps1 -rate 5

# 每秒 20 行（快速）
.\run_gnss_simulator.ps1 -rate 20
```

### Q: 如何监视接收到的数据？
在中控机系统启动后，查看日志文件：
```powershell
Get-Content logs\rtg.log -Tail 50 -Wait
```

## 📈 性能指标

| 项目 | 值 |
|------|-----|
| 标准发送速率 | 10 行/秒 |
| 每行间隔 | 100ms |
| 每个 NMEA 文件行数 | ~3,000-5,000 行 |
| 循环周期 | ~5-8 分钟 |
| 平均帧大小 | 80 字节 |
| 吞吐量 | ~800 字节/秒 |

## 🎯 与中控机集成

当运行以下配置时，模拟器完全可以替代真实硬件：

1. **启动模拟器**
   ```powershell
   # 在终端 1-3 中分别启动 3 个接收机
   .\run_gnss_simulator.ps1 -receiver_id "receiver_a1"
   .\run_gnss_simulator.ps1 -receiver_id "receiver_a2"  
   .\run_gnss_simulator.ps1 -receiver_id "receiver_trolley"
   ```

2. **启动中控机**
   ```powershell
   # 在终端 4 中启动
   .\run_controller.ps1
   ```

3. **验证数据流**
   中控机日志中应该看到：
   ```
   ✓ DriverNode 启动，共 3 台 GNSS 接收机 + 1 台 IMU
   → 启动驱动节点 (DriverNode)...
   ✓ DriverNode 启动完成
   ```

## 📚 扩展功能

要添加 IMU 模拟器，参考 GNSS 模拟器的结构，创建 `imu_simulator.py`。

---

**最后更新：2026-05-13**  
**作者：GitHub Copilot**

