# RTG 高精度定位管理系统 - 前端

## 项目简介

基于 React + TypeScript + Vite 的 RTG（轨道吊）定位系统管理前端，提供实时数据监控、场地建图、车辆校准等功能。

**业务主线**：RTK 为场地标准 → 各车独立静态刚体 → 首车全场逐贝标定 → 换车 3 点验证车际转换并复用首车全场数据 → 作业。详见下文「车辆校准」与「完整业务流程」。

## 技术栈
- **框架**: React 18.3
- **语言**: TypeScript 5.5
- **构建工具**: Vite 5.4
- **样式**: Tailwind CSS 3.4
- **图表**: Recharts 2.12
- **图标**: Lucide React
- **实时通信**: WebSocket

## 功能模块

### 1. 仪表盘 (Dashboard)
- 实时 GPS/本地坐标显示
- 姿态数据（航向、俯仰、横滚）
- 位置轨迹图表
- GNSS 接收机状态监控
- RTG 门架实时可视化

### 2. 工作配置 (Settings)
- 车辆选择与管理
- 场地选择（支持多堆场）
- 后端服务连接配置
- 模拟数据模式切换
- 实时连接状态显示

### 3. 参数配置 (VehicleConfig)
- RTG 物理参数设置（高度、跨距）
- 天线杆臂向量配置（支持最多 6 根天线）
- 参数导入/导出
- 实时同步至后端数据库

### 4. 场地建图 (Mapping) — 步骤① RTK 真值
- 手持 RTK 全场逐贝打点（手动录入 / CSV 导入）
- 写入 `survey_points`（`point_type=mapping`），不采集车载坐标
- 有真值后场地标记 `mapped=true`

### 5. 车辆校准 (Calibration) — 步骤③
- 系统输出的是 **经本车刚体变换后的作业点坐标**，不是裸天线坐标；**RTK 贝位真值为唯一空间标准**
- **校准1（full）**：首车（如 RTG-001）→ 全场逐贝对比「解算输出 vs RTK」，建立 **该车在全场的逐贝数据**（允许与 RTK 有偏差，表里记录的是偏差）
- **校准2（verify）**：换车（如 RTG-002）→ 各做静态参数后，仅在 **前 3 个 RTK 贝位** 求验 **新车→首车的坐标转换**；通过后 **复用首车全场逐贝数据**，无需再扫全场
- **校准3（skip）**：本车在该场已 `completed` → 可改点/追加，或直接作业

### 6. 系统说明 (Manual)
- 系统架构说明
- 工作流程文档
- API 接口文档
- WebSocket 数据格式

## 快速开始

### 安装依赖

```bash
cd fronted
npm install
```

### 开发模式

```bash
npm run dev
```

启动后访问 `http://localhost:5173`

### 生产构建

```bash
npm run build
```

构建产物位于 `dist/` 目录

### 预览构建结果

```bash
npm run preview
```

## 架构设计

### 数据流

```
前端 (React)  <--WebSocket-->  后端服务  <--MQTT-->  中控机程序
     │                              │                      │
     │                           SQLite                GNSS/IMU
     │                        车模/底图数据              接收机
     └──────────────────实时坐标推送───────────────────────┘
```

### 状态管理

使用 Context API 进行全局状态管理：

- **AppContext**: 车辆列表、场地列表、选中状态、连接配置
- **useWebSocket**: WebSocket 连接管理、实时数据接收、自动重连

### WebSocket 通信

前端通过统一的后端服务接入：

```typescript
ws://${backendConfig.host}:${backendConfig.port}/ws/realtime?vehicle_id=${selectedVehicleId}
```

后端负责：
- 订阅对应车辆的 MQTT Topic
- 数据格式转换与验证
- 推送实时定位数据至前端

## 主要组件

| 组件名 | 文件 | 说明 |
|--------|------|------|
| App | `src/App.tsx` | 应用主入口，路由管理 |
| Navbar | `src/components/Navbar.tsx` | 顶部导航栏，状态指示 |
| Dashboard | `src/components/Dashboard.tsx` | 实时数据仪表盘 |
| Settings | `src/components/Settings.tsx` | 工作配置页面 |
| VehicleConfig | `src/components/VehicleConfig.tsx` | 车辆参数配置 |
| Mapping | `src/components/Mapping.tsx` | 场地建图页面 |
| Calibration | `src/components/Calibration.tsx` | 车辆校准页面 |
| Manual | `src/components/Manual.tsx` | 系统说明文档 |
| common | `src/components/common.tsx` | 通用组件库 |

## 配置文件

### Vite 配置 (`vite.config.ts`)

```typescript
server: {
  port: 5173,
  proxy: {
    '/api': 'http://localhost:8000',  // 后端 API 代理
    '/ws': {
      target: 'ws://localhost:8000',
      ws: true,
    },
  },
}
```

### Tailwind 配置 (`tailwind.config.js`)

自定义主题色、动画、网格背景等。

## API 接口

### HTTP REST

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/vehicle/config` | POST/GET | 车辆参数存取 |
| `/api/survey/confirm` | POST | RTK 建图（mapping）或车辆校准采点（calibration） |
| `/api/survey/batch_import` | POST | CSV 批量导入 RTK 真值 |
| `/api/yard/map` | GET | 获取 RTK + 校准聚合数据 |
| `/api/survey/finalize` | POST | 完成校准（含 002 求 M） |

### WebSocket

**订阅地址**: `ws://host:port/ws/realtime?vehicle_id=xxx`

**数据格式**:

```json
{
  "timestamp": "2026-05-11T10:30:45.123Z",
  "gps_coord": { "lat": 22.123456, "lon": 114.234567, "alt": 12.345 },
  "local_coord": { "x": 123.45, "y": 234.56, "z": 1.23 },
  "heading": 45.6,
  "roll": 0.25,
  "pitch": 0.12,
  "speed": 0.35,
  "status": "fix|float|no_signal",
  "receivers": [...]
}
```

## 开发注意事项

### 模拟数据模式

前端内置模拟数据生成器，可在无后端服务时独立开发调试：

1. 在 Settings 页面开启"模拟数据模式"
2. 系统自动生成仿真定位数据（10Hz 更新）
3. 所有功能页面均可正常使用

### 离线存储

- 建图数据优先存储至本地
- 支持服务器离线时的本地操作
- 云端同步为可选操作

### 类型安全

所有数据结构均在 `src/types/index.ts` 中定义，享受完整的 TypeScript 类型检查。

## 常见问题

### Q: WebSocket 连接失败？

**A**: 检查以下几点：
1. 后端服务是否正常运行
2. Settings 中的后端地址是否正确
3. 是否选择了有效的车辆 ID
4. 可临时开启"模拟模式"继续开发

### Q: 为什么场地建图页面不可用？

**A**: 需要满足以下条件：
1. 已在 Settings 中选择场地
2. 该场地状态为"未建图"
3. 如已建图可点击"强制重新建图"

### Q: 车辆校准要求？

**A**: 必须满足：
1. 已选择车辆和场地，且该车已完成 **步骤② 静态参数**（`vehicle_configs`）
2. 场地已有 RTK 真值（步骤①）
3. **校准1**：首车需覆盖全部 RTK 贝位后 `finalize`
4. **校准2**：换车只需 **3 个 RTK 贝位** 采点 + `finalize`（用于验证新车相对首车的转换，并继承首车全场数据）
5. **校准3**：同车已校过可直接作业；改贝位后需重新 `finalize`

## 性能优化建议

1. 代码分割：使用 `React.lazy` + `Suspense` 按需加载页面组件
2. 图表优化：Recharts 仅渲染最近 60 帧数据
3. WebSocket：自动重连机制，断线时降级使用模拟数据
4. 构建优化：已配置 Vite 生产构建优化

## 项目结构

```
fronted/
├── public/
│   └── icon.svg              # 网站图标
├── src/
│   ├── components/           # React 组件
│   │   ├── Dashboard.tsx
│   │   ├── Settings.tsx
│   │   ├── VehicleConfig.tsx
│   │   ├── Mapping.tsx
│   │   ├── Calibration.tsx
│   │   ├── Manual.tsx
│   │   ├── Navbar.tsx
│   │   └── common.tsx
│   ├── context/
│   │   └── AppContext.tsx    # 全局状态管理
│   ├── hooks/
│   │   └── useWebSocket.ts   # WebSocket 自定义 Hook
│   ├── types/
│   │   └── index.ts          # TypeScript 类型定义
│   ├── App.tsx               # 应用主入口
│   ├── main.tsx              # React 挂载入口
│   └── index.css             # Tailwind CSS + 自定义样式
├── index.html                # HTML模板
├── package.json              # 依赖配置
├── vite.config.ts            # Vite 构建配置
├── tailwind.config.js        # Tailwind 配置
└── tsconfig.json             # TypeScript 配置
```

## 版本历史

- **v2.5.0** (2026-05-11)
  - ✅ 完成所有核心功能模块
  - ✅ 统一后端服务架构
  - ✅ 完善类型系统和错误处理
  - ✅ 优化 UI/UX 交互体验

## 后续计划

- [ ] 添加地图可视化（集成高德/百度地图）
- [ ] 支持历史轨迹回放
- [ ] 添加报警规则配置
- [ ] 多语言支持（中英文）
- [ ] 移动端适配

## 各页面详细说明

### 1. 工作配置页 (Settings)

系统的**首选配置入口**，用户进入后的第一步操作。所有其他页面依赖此页面选择的车辆和场地。

#### 功能区块

| 区块 | 说明 |
|------|------|
| 模拟数据开关 | 切换模拟/真实模式。开启后使用内置仿真数据（200ms/帧），无需后端和中控机；关闭后自动从后端拉取车辆和场地列表 |
| 待初始化设备 | 展示通过 MQTT `rtg/discovery` 发现的未分配正式 ID 的设备。点击「初始化」弹窗填写正式 ID、杆臂、高度、跨距等参数，提交后调用 `POST /api/vehicle/init` |
| 后端服务连接 | 配置后端 IP:Port（默认 `192.168.1.200:8080`），显示 WebSocket 连接状态（已连接/连接中/未连接） |
| 当前工作配置 | 展示已选车辆和场地的摘要信息，并根据角色判定逻辑显示当前角色提示 |
| 车辆列表 | 左栏，从后端 `GET /api/vehicle/list` 拉取。显示车辆编号、天线数、高度 H、跨距 W。点击选中/取消 |
| 场地列表 | 右栏，从后端 `GET /api/yard/all_origins` 拉取。显示场地 ID、贝位数、朝向、是否已建图。点击选中/取消 |

#### 角色判定逻辑

| 条件 | 建议页面 | 说明 |
|------|----------|------|
| 场地无 RTK 真值（`mapped=false`） | 场地建图 | 步骤①：RTK 全场打点 |
| 有 RTK + 本车未校准 | 车辆校准 | 步骤③：首车用校准1（全场），换车用校准2（3 点） |
| 本车已校准（`calibration_status=completed`） | 仪表盘 | 校准3：直接作业 |

**校准模式**（`calibration_mode`）：`full` | `verify` | `skip`，由 `GET /api/yard/map` 或 `GET /api/calibration/mode` 返回。

---

### 2. 仪表盘页 (Dashboard)

实时定位数据的**核心监控界面**，展示中控机通过 MQTT → 后端 WebSocket 推送的全量定位结果。

#### 数据卡片

| 卡片 | 字段 | 说明 |
|------|------|------|
| L-Point 大地坐标 | `gps_coord.lat` / `lon` / `alt` | GNSS 天线经 L-Point 刚体变换后的 WGS84 坐标，精度 8 位小数（约 1mm 级） |
| 堆场坐标 (LYCS) | `local_coord.x` / `y` / `z` | 堆场局部坐标系下的 L-Point 坐标 (m)，X 沿大车行驶方向，Y 沿小车轨道方向，Z 竖直向上 |
| 实时姿态 | `heading` / `roll` / `pitch` | 航向角 ψ (°)：RTG 行驶朝向；横滚角 φ (°)：左右倾斜；俯仰角 θ (°)：前后倾斜 |
| GNSS 状态 | `status` / `speed` | `fix`=RTK 固定解（厘米级），`float`=浮动解（分米级），`no_signal`=失锁；速度单位 m/s |

#### 大车定位区域

| 字段 | 含义 |
|------|------|
| `gantry.elec_center` | 电气房侧中心距堆场原点坐标 (X, Y)，单位 m |
| `gantry.diesel_center` | 柴油机侧中心距堆场原点坐标 (X, Y)，单位 m |
| `gantry.leg_offsets.fl/fr/rl/rr` | 四个门腿（前左/前右/后左/后右）中心点相对跑道中心线的偏移量 (m)，正值偏向堆场外，负值偏向堆场内，用于大车纠偏 |
| `gantry.speed` | 大车运行速度 (m/s)，数值增大方向为正，减小为负 |

#### 小车定位区域

| 字段 | 含义 |
|------|------|
| `trolley.ant_a` / `ant_b` | 小车两端 GNSS 天线在 RTG 坐标系下的实时 X/Y/Z 坐标 (m) |
| `trolley.travel` | 小车距轨道起始点的实际运行距离 (m)，用于驱动门架示意图中小车位置 |
| `trolley.speed` | 小车运行速度 (m/s)，沿轨道数值增大方向为正 |

#### 接收机状态面板

| 字段 | 含义 |
|------|------|
| `receivers[].label` | 接收机标识，如 "Recv-1 (大车左前)" |
| `receivers[].status` | 该接收机的定位状态 (`fix` / `float` / `no_signal`) |
| `receivers[].satellites` | 当前跟踪卫星数量 |
| `receivers[].hdop` | 水平精度因子，越小越好（< 1.0 为优） |

#### 图表与可视化

- **位置轨迹图**：实时绘制最近 60 帧的 X/Y 坐标曲线（Recharts AreaChart）
- **航向罗盘**：SVG 实现的指北针，实时跟随 `heading` 旋转
- **RTG 门架示意图**：SVG 简化模型，小车位置由 `trolley.travel` 驱动（映射 0–20m 到横梁范围），显示 L-Point 标注和门腿偏移

---

### 3. 参数配置页 (VehicleConfig)

管理 RTG 的**物理几何参数**，这些参数直接影响 L-Point 刚体变换的精度。

#### 基本属性字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `vehicle_id` | string | 车辆编号（只读），如 "RTG-001" |
| `label` | string | 用户可读显示名称，如 "RTG 01号机" |
| `height` | number (m) | 标称高度 H：天线相位中心到地面的垂直设计距离 |
| `span` | number (m) | 门腿跨距 W_span：左右门腿物理中心线的水平距离 |
| `l_arm` | [X, Y, Z] (m) | 杆臂向量：天线相位中心相对于地面 L-Point 的 3D 偏移矢量，用于 `P_final = P_gps − R · L_arm` 公式 |

#### 天线杆臂阵列

每根天线独立配置（最多 6 根），对应硬件安装的实际偏移量：

| 字段 | 说明 |
|------|------|
| `label` | 天线标签，如 "Front-Left (大车左前)" |
| `dx` | X 方向偏移 (m)，沿大车行驶方向 |
| `dy` | Y 方向偏移 (m)，沿小车轨道方向 |
| `dz` | Z 方向偏移 (m)，竖直方向（通常为负值，天线在 L-Point 上方） |

#### 数据流

1. 进入页面 → `GET /api/vehicle/config/{vehicle_id}` 从后端加载最新参数
2. 编辑完成 → 点击「保存并下发至中控机」→ `POST /api/vehicle/config` 将参数写入后端数据库
3. 后端字段映射：前端 `span` → 后端 `w_span`，前端 `antennas` 数组 → 后端 `antennas` 字典

---

### 4. 场地建图页 (Mapping) — 步骤①

仅录入 **RTK 手持真值**，与车辆无关。

#### 进入条件

- 已选择场地；`mapped=false` 时可录入；已建图可「强制重新建图」

#### 工作流程

1. 手持 RTK 到达贝位，读取 lat/lon/alt
2. 贝位编号填**数字**（1、2、40…），与后端 `bay_no` 一致
3. **手动录入** → `POST /api/survey/confirm`（`vehicle_id=RTK_HANDHELD`, `mode=mapping`）
4. 或 **CSV 批量** → `POST /api/survey/batch_import`
5. 同贝位重复提交会**覆盖** RTK 坐标，不会新增重复行

---

### 5. 车辆校准页 (Calibration) — 步骤③

#### 定位链路与标准

```
GNSS/IMU → 本车静态刚体(L_arm 等) → 作业点解算输出 → 与 RTK 贝位真值对比
```

- **RTK（mapping）**：场地标准，与哪台 RTG 无关。
- **解算输出**：经 **该车** 刚体参数得到；RTG-001 与 RTG-002 静态不同，**不能共用对方杆臂**。
- **首车逐贝数据**：记录的是「该车解算相对 RTK 的偏差」，**不等于** RTK 本身；首车也可以整场对不上 RTK，但差值被记下。

#### 三种校准模式（业务含义）

| 模式 | 典型场景 | 采点 | 目的 |
|------|----------|------|------|
| **校准1** `full` | 首车 RTG-001 进场 | **全部** RTK 贝位 | 在 001 刚体下，建立 **001 相对 RTK 的全场逐贝表** |
| **校准2** `verify` | 换车 RTG-002，001 已 `completed` | **前 3 个** RTK 贝位 | 在 002 刚体下，用 3 点求验 **002→001 的坐标转换**；通过后 **沿用 001 全场逐贝数据**，002 不必再跑 40 贝 |
| **校准3** `skip` | 同车再来 | 可选改点 | 已 `completed`，可直接作业 |

**为何 002 只需 3 点？**  
平面刚体变换（平移 + 旋转）由 **3 个非共线已知点** 即可确定/验收。002 与 001 的静态参数已知后，3 个 RTK 贝位足以验证「002 解算链 + 车际转换」是否可靠；**场地逐贝关系** 已由 001 全场标定承载，002 通过对齐 001 复用。

#### 前端操作（当前实现）

- 选择贝位 → `POST /api/survey/confirm`（`mode: calibration`，提交当前解算坐标）
- 可 **覆盖/追加** 任意贝位；**清空重新校准** → `DELETE /api/calibration/transform_list`
- 采点足够后 → `POST /api/survey/finalize`（`verify` 至少 3 贝，`full` 需覆盖全部 RTK 贝位）

#### 数据表

| 存储 | 说明 |
|------|------|
| `vehicle_bay_transforms` | 某车在某贝：`ref`=RTK，`meas`=该车解算，`delta`=meas−ref |
| `calibration_data` | `finalize` 后 `completed`；`m_matrix` 为完成时快照 |

查询：`GET /api/calibration/transform_list?vehicle_id&yard_id`（注意 `yard_id` 与建图一致，如 `yard_01`）

#### 实现说明（文档与代码）

- **目标架构**：校准2 应显式保存「参考车 ID」（`source_vehicle_id`），作业时 **002 输出 → 变换到 001 坐标系 → 套用 001 逐贝数据**。
- **当前后端/前端**：已支持逐贝采点、`verify` 少采门槛与 `reference_vehicle_id` 查询；**车际转换复用 001 全场表** 的完整解算链路在中控侧尚待打通（见根目录 `README.md` / `PROJECT_SUMMARY.md`）。

---

## 完整业务流程

```
步骤① 场地真值（Mapping）
  手持 RTK 全场打点 → survey_points (mapping)
  → yards.mapped = true

步骤② 车体静态（VehicleConfig）
  杆臂、跨距、天线标识等 → vehicle_configs

步骤③ 场地校准（Calibration）
  校准1：首车(001) → 全场逐贝：记录「001解算 vs RTK」→ vehicle_bay_transforms
  校准2：新车(002) → 静态各做 + 3贝求验 002→001 → 复用001全场数据 → finalize
  校准3：同车再来 → 可改点或直作业
  → finalize → calibration_data (status=completed)

步骤④ 作业（Dashboard）
  WebSocket 实时定位
```

---

### 6. 系统说明页 (Manual)

内嵌的操作手册，包含系统架构、工作流程、API 文档和 WebSocket 数据格式说明。纯展示页面，不涉及后端交互。

---

## 全局状态管理 (AppContext) 字段说明

| 状态字段 | 类型 | 说明 |
|----------|------|------|
| `vehicles` | `VehicleProfile[]` | 所有已注册（已初始化）的车辆列表 |
| `pendingDevices` | `PendingDevice[]` | 尚未分配正式 ID 的设备列表（`is_initialized=false`） |
| `yards` | `YardInfo[]` | 所有堆场列表，含 `mapped` 状态 |
| `selectedVehicleId` | `string \| null` | 当前选中的车辆 ID，`null` 表示未选择 |
| `selectedYardId` | `string \| null` | 当前选中的场地 ID，`null` 表示未选择 |
| `useMock` | `boolean` | 是否使用模拟数据模式（默认 `true`） |
| `backendConfig` | `{ host, port }` | 后端服务地址，默认 `192.168.1.200:8080` |
| `connStatus` | `'connecting' \| 'connected' \| 'disconnected'` | WebSocket 连接状态 |
| `isMock` | `boolean` | 当前是否实际处于模拟模式（由 useWebSocket 同步写入） |

### 关键方法

| 方法 | 说明 |
|------|------|
| `switchMockMode(enable)` | 切换模拟/真实模式。开启 → 还原默认演示数据；关闭 → 清空列表并从后端拉取 |
| `fetchVehicles()` | 调用 `GET /api/vehicle/list`，区分已初始化/未初始化设备 |
| `fetchYards()` | 调用 `GET /api/yard/all_origins`，获取所有堆场 |
| `initDevice(params)` | 调用 `POST /api/vehicle/init`，为新设备分配正式 ID 和参数 |
| `updateVehicle(v)` | 更新本地车辆参数（配合 VehicleConfig 页保存操作） |
| `markYardMapped(yardId)` | 将指定场地标记为已建图 |

---

## WebSocket 实时数据完整格式

```json
{
  "timestamp": "2026-05-11T10:30:45.123Z",
  "gps_coord": {
    "lat": 22.12345678,
    "lon": 114.23456789,
    "alt": 12.345
  },
  "local_coord": {
    "x": 123.4567,
    "y": 234.5678,
    "z": 1.2345
  },
  "heading": 180.50,
  "roll": 0.250,
  "pitch": 0.120,
  "speed": 0.350,
  "status": "fix",
  "receivers": [
    {
      "id": "recv1",
      "label": "Recv-1 (大车左前)",
      "position": "FL",
      "status": "fix",
      "satellites": 14,
      "hdop": 0.8
    }
  ],
  "gantry": {
    "elec_center": { "x": 123.45, "y": 5.12 },
    "diesel_center": { "x": 123.45, "y": -5.12 },
    "leg_offsets": {
      "fl": 0.008,
      "fr": -0.012,
      "rl": 0.010,
      "rr": -0.009
    },
    "speed": 0.350
  },
  "trolley": {
    "ant_a": { "x": 8.85, "y": 0.30, "z": -28.50 },
    "ant_b": { "x": 8.15, "y": -0.30, "z": -28.50 },
    "travel": 8.50,
    "speed": 0.250
  }
}
```

### 字段单位与精度汇总

| 字段 | 单位 | 典型精度 | 备注 |
|------|------|----------|------|
| `lat` / `lon` | 度 (°) | 8 位小数 (≈1mm) | WGS84 大地坐标 |
| `alt` | 米 (m) | 3 位小数 | 海拔高度 |
| `local_coord.*` | 米 (m) | 4 位小数 | 堆场局部坐标系 |
| `heading` | 度 (°) | 2 位小数 | 0–360°，正北为 0 |
| `roll` / `pitch` | 度 (°) | 3 位小数 | 通常 < ±2° |
| `speed` | m/s | 3 位小数 | 正值=数值增大方向 |
| `leg_offsets.*` | 米 (m) | 4 位小数 | < 0.05m 为正常 |
| `trolley.travel` | 米 (m) | 3 位小数 | 小车行程 |
| `hdop` | 无量纲 | 1 位小数 | < 1.0 为优 |

---

## 前端调用后端 API 完整清单

| 调用位置 | 接口 | 方法 | 请求参数 | 说明 |
|----------|------|------|----------|------|
| AppContext `fetchVehicles` | `/api/vehicle/list` | GET | — | 拉取车辆列表（含初始化状态） |
| AppContext `fetchYards` | `/api/yard/all_origins` | GET | — | 拉取所有堆场列表 |
| AppContext `initDevice` | `/api/vehicle/init` | POST | `{ temp_id, new_vehicle_id, label, params: { l_arm, height, w_span } }` | 初始化新设备 |
| Settings 角色判定 | `/api/yard/map` | GET | `?yard_id=xx&vehicle_id=xx` | 查询校准状态 |
| VehicleConfig 加载 | `/api/vehicle/config/{id}` | GET | — | 获取车辆参数 |
| VehicleConfig 保存 | `/api/vehicle/config` | POST | `{ vehicle_id, l_arm, height, w_span, antennas }` | 保存车辆参数 |
| Mapping 历史加载 | `/api/survey/history` | GET | `?yard_id=xx` | 获取打点历史 |
| Mapping RTK 打点 | `/api/survey/confirm` | POST | `{ vehicle_id: "RTK_HANDHELD", mode: "mapping", lat, lon, alt }` | RTK 真值 |
| Mapping CSV | `/api/survey/batch_import` | POST | `{ yard_id, points[] }` | 批量 RTK |
| Calibration 采点 | `/api/survey/confirm` | POST | `{ vehicle_id, mode: "calibration", lat, lon, alt, local_x/y/z }` | 写入逐贝转换列表 |
| Calibration 完成 | `/api/survey/finalize` | POST | `{ vehicle_id, yard_id }` | 完成校准，保存 M + transform_list |
| 转换列表 | `/api/calibration/transform_list` | GET | `?vehicle_id&yard_id` | 本车逐贝转换记录 |
| 校准模式 | `/api/calibration/mode` | GET | `?vehicle_id&yard_id` | full / verify / skip |

## 许可证

Private & Proprietary

