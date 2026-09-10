# RTG 中控机程序说明文档

## 概述

RTG 中控机程序是高精度自动定位系统的核心计算单元，负责：
1. **数据采集**：接收 GNSS 接收机和 IMU 的原始数据
2. **融合解算**：运行 EKF 融合算法，输出平滑位姿
3. **坐标转换**：执行 WGS84 → LYCS 转换、L-Point 刚体变换
4. **通信下发**：向 PLC 下发定位结果（10Hz），与后端服务 MQTT 通信
5. **设备管理**：支持身份注册、配置同步、建图校准

---

## 新增功能（v2.0）

### 1. MQTT 通信
- **Topic 设计**：
  - `rtg/{vehicle_id}/telemetry`：实时定位数据（10Hz）
  - `rtg/{vehicle_id}/control`：控制指令接收
  - `rtg/{vehicle_id}/status`：设备状态上报
  - `rtg/discovery`：新设备发现广播

### 2. 设备身份管理
- 初次启动自动生成临时 ID（`RTG-NEW-xxxx`）
- 前端初始化后接收正式 ID（`RTG-001`）
- ID 持久化到 `config/system.yaml`

### 3. 后端 API 集成
- 启动时从云端拉取车辆配置
- 支持建图/校准数据上传
- 打点确认接口对接

### 4. 自动堆场识别
- 根据 GNSS 坐标自动匹配所在堆场
- 动态加载对应的转换矩阵和参数

---

## 系统架构

```
┌─────────────────┐
│  GNSS 接收机 x3 │ ──UDP push──┐
└─────────────────┘              │
                                 ↓
┌─────────────────┐        DriverNode（中控 bind 9001/9002/9003）
│   IMU 传感器    │ ──UDP──→     │
└─────────────────┘              ▼
                          EventBus → FusionNode (EKF)
                                        │
                                        ↓
                                SolverNode (坐标转换)
                                        │
                                        ├──→ BridgeNode → PLC (TCP)
                                        │
                                        └──→ MqttClient → 后端 Broker
```

**GNSS 通信说明**：中控在本机绑定 UDP 端口并监听；三台接收机向中控 IP 的对应端口推送 NMEA。`receiver_id`（gantry_a1 / gantry_a2 / trolley）由**端口**决定，不是 NMEA 内容。配错会导致多路数据混入同一通道，详见 [docs/RECEIVER_UDP_ARCHITECTURE.md](docs/RECEIVER_UDP_ARCHITECTURE.md)。

---

## 配置文件说明

### config/system.yaml
```yaml
# 设备 ID（初始化后自动填充）
vehicle_id: "RTG-001"

# 后端服务
backend:
  host: "192.168.1.100"
  port: 8000
  api_base_url: "http://192.168.1.100:8000"

# MQTT Broker
mqtt:
  broker_host: "192.168.1.100"
  broker_port: 1883
  username: null
  password: null

# GNSS：中控在本机 bind 端口；host 为监听地址，不是接收机 IP
gnss:
  receiver_a1:  # 左列 → gantry_a1
    host: "0.0.0.0"
    port: 9001
    role: gantry_a1
  receiver_a2:  # 右列 → gantry_a2
    host: "0.0.0.0"
    port: 9002
    role: gantry_a2
  receiver_b:   # 小车 → trolley
    host: "0.0.0.0"
    port: 9003
    role: trolley
# 接收机侧配置：目标 IP = 中控机 IP，目标端口 = 9001 / 9002 / 9003（各不同）

# PLC 通信
plc:
  host: "192.168.1.200"
  port: 5000
  frame_rate_hz: 10
```

### config/vehicle_params.yaml
```yaml
gantry:
  antenna_a1:
    L_arm: [0.12, 0.08, -8.45]  # 杆臂向量 [dx, dy, dz]
  antenna_a2:
    L_arm: [0.12, 0.08, -8.45]
  H: 8.45          # 标称高度
  W_span: 20.0     # 跨距

trolley:
  antenna_c1:
    L_arm: [0.05, 0.50, -3.10]
  antenna_c2:
    L_arm: [0.05, -0.50, -3.10]
  H: 3.10

ekf:
  sigma_pos: 0.05
  sigma_vel: 0.01
  gnss_outage_max_s: 10.0
```

### config/yard_config.yaml
```yaml
yards:
  - id: "yard_01"
    name: "一号堆场"
    origin:
      lat: 22.345678
      lon: 113.987654
      alt: 5.20
    heading_deg: 87.5
    bay_spacing: 6.058
    bay_count: 20
```

---

## 启动流程

### 1. 安装依赖
```bash
cd D:\kpl\pycharmproject\RTG
pip install -r requirements.txt
```

### 2. 首次启动（未初始化设备）
```bash
python -m src.main
```

**日志输出示例**：
```
[INFO] RTG 高精度自动定位系统启动
[WARN] ⚠ 设备未初始化，使用临时 ID: RTG-NEW-8b36
[INFO] ✓ 后端服务连接正常: http://192.168.1.100:8000
[INFO] MQTT 连接中: 192.168.1.100:1883
[INFO] 已发送设备发现广播: {'temp_id': 'RTG-NEW-8b36', ...}
```

### 3. 前端初始化后
前端在 Settings 页面为设备分配正式 ID（如 `RTG-001`）后，中控机接收 MQTT 指令：
```json
{
  "cmd": "CMD_INIT_IDENTITY",
  "data": {
    "new_vehicle_id": "RTG-001"
  }
}
```

中控机保存 ID 到 `system.yaml` 并重启。

### 4. 正常启动（已初始化设备）
```bash
python -m src.main
```

**日志输出示例**：
```
[INFO] ✓ 设备已初始化，Vehicle ID: RTG-001
[INFO] 正在从云端同步配置...
[INFO] ✓ 云端配置同步成功
[INFO] 系统启动完成，所有节点运行中
```

---

## MQTT 消息示例

### 遥测数据（Telemetry）
**Topic**: `rtg/RTG-001/telemetry`  
**QoS**: 0  
**Payload**:
```json
{
  "timestamp": 1715395200.123,
  "yard_id": "yard_01",
  "gantry": {
    "center_elec": 45.67,
    "center_engine": 45.68,
    "lpoint_x": 45.675,
    "lpoint_y": 1.23,
    "lpoint_z": 0.05,
    "speed": 0.35,
    "heading": 87.5,
    "leg_offsets": [1.20, 1.22, 21.20, 21.22]
  },
  "trolley": {
    "center_x": 12.34,
    "center_y": 5.67,
    "center_z": 3.10,
    "travel_distance": 8.90,
    "speed": 0.15
  },
  "status": {
    "fix_quality": 4,
    "imu_coasting": false,
    "imu_coasting_duration": 0.0
  }
}
```

### 控制指令（Control）
**Topic**: `rtg/RTG-001/control`  
**QoS**: 1  

建图/校准**采点**在 Web 完成（`POST /api/survey/confirm`），不经 MQTT。中控仅接收配置与校准同步类指令。

#### 校准完成同步
```json
{
  "cmd": "CALIBRATION_FINALIZED",
  "data": {
    "vehicle_id": "RTG-002",
    "yard_id": "A01",
    "status": "completed"
  }
}
```

#### 设备初始化
```json
{
  "cmd": "CMD_INIT_IDENTITY",
  "data": {
    "new_vehicle_id": "RTG-002",
    "params": { "l_arm": [0.0, 0.0, 0.0], "height": 20.0, "w_span": 23.47 }
  }
}
```

#### 配置更新
```json
{
  "cmd": "UPDATE_CONFIG",
  "data": {
    "l_arm": [0.12, 0.08, -8.45],
    "h": 8.45,
    "w_span": 20.0
  }
}
```

### 状态上报（Status）
**Topic**: `rtg/RTG-001/status`  
**QoS**: 1  
**Payload**:
```json
{
  "event": "yard_changed",
  "current_yard": "yard_01",
  "timestamp": 1715395200.123,
  "mode": "working",
  "hardware": {
    "gnss": "ok",
    "imu": "ok",
    "plc": "connected"
  }
}
```

---

## 故障排查

### 问题 1：MQTT 连接失败
**现象**：日志显示 `MQTT 连接失败: Connection refused`

**解决**：
1. 检查后端 MQTT Broker 是否运行
2. 确认 `system.yaml` 中的 `mqtt.broker_host` 和 `mqtt.broker_port`
3. 检查防火墙是否开放 1883 端口

### 问题 2：后端 API 不可达
**现象**：`⚠ 后端服务暂时不可达`

**解决**：
1. 确认后端服务已启动（`python backend/app.py`）
2. 浏览器访问 `http://192.168.1.100:8000/api/health` 测试
3. 检查网络连通性：`ping 192.168.1.100`

### 问题 3：GNSS 数据无输出
**现象**：日志无 GNSS 数据，或显示 `fix_quality=0`，或某路 `[gantry_a2/trolley] 超过 2.0 s 未收到数据`

**解决**：
1. 确认中控已监听：`DriverNode 启动` 日志中应有三路 `GNSS [...] UDP 0.0.0.0:900x`
2. 确认每台接收机**目标 IP = 中控 IP**，且端口分别为 9001 / 9002 / 9003（不可相同）
3. 查看 `[gantry_a1]` 等日志中的 `收到 UDP ... 来自 <IP>`，核对来源 IP 与物理接收机一致
4. 若某路日志出现两个不同源 IP，说明多机共端口，见 [docs/RECEIVER_UDP_ARCHITECTURE.md](docs/RECEIVER_UDP_ARCHITECTURE.md)

### 问题 4：PLC 无法连接
**现象**：`✗ PLC连接: ✗`

**解决**：
1. 确认 PLC IP 和端口配置正确
2. 检查 PLC 是否已打开 TCP Server
3. 使用工具测试连接：`Test-NetConnection -ComputerName 192.168.1.200 -Port 5000`

---

## 性能指标

| 指标                  | 目标值       | 实际值       |
|-----------------------|--------------|--------------|
| GNSS 数据刷新率       | ≥10Hz        | 10Hz         |
| IMU 数据刷新率        | 200Hz        | 200Hz        |
| PLC 下发帧率          | ≥10Hz        | 10Hz         |
| MQTT 遥测频率         | 10Hz         | 10Hz         |
| 定位精度（RTK Fixed） | ±30mm 水平   | 达标         |
| EKF 融合延迟          | <100ms       | <50ms        |
| 丢星补偿时长          | ≤10s         | 10s          |

---

## 开发调试

### 运行单元测试
```bash
# 坐标转换测试
python -m pytest tests/test_coordinate_transform.py

# L-Point 解算测试
python -m pytest tests/test_lpoint_solver.py

# 协议编解码测试
python -m pytest tests/test_protocol.py
```

### 模拟器测试
```bash
# 启动 GNSS/IMU 数据模拟器
python tools/simulator.py
```

### 诊断工具
```bash
# 系统诊断（检查所有硬件连接）
python tools/diagnostic.py
```

---

## 更新日志

### v2.0 (2026-05-11)
- ✅ 新增 MQTT 通信模块
- ✅ 新增后端 API 客户端
- ✅ 新增设备身份管理
- ✅ 支持设备自动发现
- ✅ 支持配置云端同步
- ✅ 优化启动流程

### v1.0 (2026-04-01)
- ✅ 基础定位功能
- ✅ EKF 融合算法
- ✅ L-Point 刚体变换
- ✅ PLC 通信协议

---

## 技术支持

如有问题，请查看日志文件：`logs/rtg.log`

联系方式：技术团队

