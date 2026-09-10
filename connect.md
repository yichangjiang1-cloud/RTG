RTG 高精度定位系统交互设计 (多机扩展版)

1. 身份赋予机制 (Registration & Identity)
为了支持几十台 RTG 的快速部署，采用“前端命名-后端下发-本地持久化”的机制：
临时身份：新中控机出厂默认以 RTG-NEW-[MAC地址] 为临时 ID 连接 MQTT。
身份证号 (Vehicle ID)：用户在前端输入参数并命名（如 RTG-001）。
本地固化：中控机接收到配置后，将 vehicle_id 写入本地 config/system.yaml，从此拥有永久身份证，重启不丢失。

2. 第一阶段：设备初始化注册 (Initial Commissioning)
这是新设备第一次进入系统时的流程。
步骤            参与方                                    动作描述
1               中控机              首次启动，读取 MAC 地址，以 RTG-NEW-XXXX 身份上线 MQTT。
2               前端                    在“待选设备”列表中发现新设备，用户点击“初始化”。
3               前端            用户输入车型静态参数 ($L_{arm}, H, W_{span}$) 并赋予正式名称（如 RTG-001）。
4               后端                记录该车信息，通过 MQTT 发送 CMD_INIT_IDENTITY 指令给临时 ID。
5               中控机          收到指令，执行 save_to_local()，将 ID 和参数写入 system.yaml 并重启服务。
6               中控机                重启后：以新 ID RTG-001 重新上线，从此具备正式身份。

3. 第二阶段：日常启动与自发现 (Startup & Auto-Discovery)
设备完成注册后的日常启动逻辑。
步骤            动作类型                                                    描述
1               身份声明                            中控机读取本地 system.yaml 里的正式 ID，连接 MQTT。
2               配置对齐                中控机向后端发送 GET /api/vehicle/config?id={id} 确认参数是否有远程修改（以云端为准）。
3               堆场感知                        请求 GET /api/yard/all_origins，对比当前 GNSS 坐标，自动匹配所在堆场。
4               状态汇报                      汇报：{"current_yard": "yard_01", "mode": "working", "calibrated": true}。

4. 第三阶段：场地判定与角色分配 (Role Assignment)
后端根据 vehicle_id 和 yard_id 的历史记录判定当前任务：
角色 A (Surveyor)：该场地在 DB 中没有 M 矩阵（即没有 A 车来过）。
角色 B (Calibrator)：有底图，但该 vehicle_id 在该场地没校准过。
角色 C (Worker)：该 vehicle_id 在该场地已完成校准，直接进入高频作业模式。

5. 第四阶段：建图与校准流程 (Survey & Calibration)
核心原则：采点与矩阵计算在 Web 后端，中控负责实时解算与消费校准结果。

建图（RTK 真值）：Mapping 页录入或 confirm(mode=mapping) → survey_points（vehicle_id=RTK_HANDHELD，不经中控）。
不使用中控建图：无 generate_map / 中控逐贝录 RTK 流程。
车辆校准（001 全场 / 002 三点 verify）：
  1. 中控持续 MQTT 遥测 → 后端 → 前端 WebSocket 展示 local_coord。
  2. 用户在前端 Calibration 页点「确认贝位」→ POST /api/survey/confirm（提交当前 L-Point 与 local_x/y/z）。
  3. 后端写入 vehicle_bay_transforms；finalize 时求 M（002）或标记 completed（001）。
  4. 后端 MQTT 下发 CALIBRATION_FINALIZED → 中控 reload_calibration，作业套用 M + 参考车全场 delta。

不使用 RECORD_POINT：中控不接收「后端推送真值再本地采点」指令。

6. MQTT 协议定义（带身份路由）
注册流：rtg/discovery（新设备广播身份）。
实时流：rtg/{vehicle_id}/telemetry（中控机推送实时坐标）。
控制链：rtg/{vehicle_id}/control（配置更新、校准完成通知、设备初始化等；不含采点）。
状态链：rtg/{vehicle_id}/status（中控机汇报健康度/当前场地）。

7. 职责总结
前端 (React)
命名与初始化：作为“入场引导”，为新设备分配 ID。
实时看板：根据 vehicle_id 订阅对应数据，在底图上实时绘图。

后端 (Backend)
身份中心：维护 ID 与 MAC 地址的映射，确保存储的数据（参数、矩阵）都能通过 ID 索引。
校准存证：接收 Web 采点、计算 M 矩阵、持久化 transform_list 与 calibration_data。
中控机 (Python Core)
持久化存储：负责将后端下发的“身份证”与堆场校准备份保存到本地。
实时解算：GNSS/IMU → L-Point → 套用 API 同步的 M 与逐贝残差；MQTT 上报遥测供 Web 采点。



RTG 高精度定位系统接口定义文档 (V2.0)

1. 车辆管理与初始化 (Vehicle & Identity)
1.1 获取在线车辆列表
接口: GET /api/vehicle/list
说明: 前端获取当前所有在线（含待初始化）的设备。
返回数据:
{
  "code": 200,
  "data": [
    {
      "vehicle_id": "RTG-001",
      "mac": "00:0c:29:4f:8b:35",
      "status": "online",
      "is_initialized": true,
      "last_seen": "2026-05-11 10:00:00"
    },
    {
      "vehicle_id": "RTG-NEW-8b36",
      "mac": "00:0c:29:4f:8b:36",
      "status": "online",
      "is_initialized": false
    }
  ]
}

1.2 设备初始化与重命名
接口: POST /api/vehicle/init
说明: 前端为新发现的设备分配正式 ID 并写入静态参数。
请求体:
{
  "temp_id": "RTG-NEW-8b36",
  "new_vehicle_id": "RTG-002",
  "params": {
    "l_arm": [1.5, 0.5, 3.2],
    "h": 22.5,
    "w_span": 23.47
  }
}

1.3 获取/修改车型参数
接口: GET/POST /api/vehicle/config
参数: ?vehicle_id=RTG-001
数据结构:
{
  "l_arm": [1.5, 0.5, 3.2], // 杆臂向量 [x, y, z]
  "h": 22.5,                // 吊具基准高度
  "w_span": 23.47,          // 跨距
  "ekf_params": { ... }      // 可选：算法高级参数
}

2. 堆场与地图管理 (Yard & Map)
2.1 获取所有堆场原点 (用于自发现)
接口: GET /api/yard/all_origins
说明: 中控机启动后拉取，用于对比距离自动匹配场地。
返回数据:
[
  { "yard_id": "yard_01", "name": "东区1号堆场", "origin": [22.123, 114.456] },
  { "yard_id": "yard_02", "name": "西区2号堆场", "origin": [22.125, 114.459] }
]

2.2 获取堆场底图与校准态
接口: GET /api/yard/map
参数: ?yard_id=A01&vehicle_id=RTG-001
说明: 聚合 RTK survey_points、本车 transform_list、calibration_data（含 m_matrix）。
      建图真值请用 3.1 mapping；车辆 M 矩阵请用 3.2 finalize 写入。

3. 打点与校准控制 (Survey & Calibration)
3.1 确认到达贝位 (Web 采点)
接口: POST /api/survey/confirm
说明: 前端从 WebSocket 读取当前车实时解算坐标后提交；后端匹配 RTK 真值或参考车同贝坐标。
请求体（校准示例）:
{
  "vehicle_id": "RTG-002",
  "yard_id": "A01",
  "bay_id": "8",
  "mode": "calibration",
  "lat": 22.1234567,
  "lon": 114.6543210,
  "alt": 12.5,
  "local_x": 48.2,
  "local_y": 3.1,
  "local_z": 0.0
}

3.2 校准状态终审
接口: POST /api/survey/finalize
说明: 001 全场或 002 至少 3 贝 verify 通过后，后端计算 M（002）、标记 completed，并 MQTT 通知中控 reload。

4. MQTT 实时消息协议 (Real-time Flow)
4.1 实时高频坐标流 (Telemetry)
Topic: rtg/{vehicle_id}/telemetry
频率: 10Hz
Payload:
{
  "ts": 1715395200000,
  "pos": { "x": 12.34, "y": 45.67, "z": 1.23 },
  "heading": 90.5,
  "status": "fix", // fix, float, single
  "diff": { "dx": 0.02, "dy": -0.01 } // 打点时的实时残差
}

4.2 控制指令链 (Control)
Topic: rtg/{vehicle_id}/control
常用 cmd（采点不在此列，见 3.1）:
  - CMD_INIT_IDENTITY   设备绑定正式 ID
  - UPDATE_CONFIG       热更新杆臂/姿态等
  - CALIBRATION_FINALIZED  finalize 后触发中控同步校准
  - SET_TROLLEY_ORIGIN  小车零点
示例:
{
  "cmd": "CALIBRATION_FINALIZED",
  "data": { "vehicle_id": "RTG-002", "yard_id": "A01", "status": "completed" }
}

4.3 状态汇报链 (Status)
Topic: rtg/{vehicle_id}/status
Payload:
{
  "mode": "survey|calibrate|work",
  "current_yard": "yard_01",
  "hardware": { "gnss": "ok", "imu": "ok", "plc": "connected" }
}

5. 数据类型定义汇总
类型名称                    基础格式                              说明
Matrix3x3                 number[][]                    3行3列数组，用于旋转平移矩阵
Vector3             [number, number, number]              三维空间向量 (x, y, z)
VehicleID                   string                        唯一标识符，如 "RTG-001"
GPSPoint               [number, number]                       [经度, 纬度]
StatusEnum                  string                "fix" (固定解), "float" (浮动解), "none" (无信号)