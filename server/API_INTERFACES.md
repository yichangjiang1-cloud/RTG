# RTG 后端服务 - API 接口文档

## 概览

| 模块 | 接口数 | 说明 |
|------|--------|------|
| 车辆管理 | 6 | 设备初始化、参数配置、配置克隆 |
| 堆场管理 | 5 | 场地创建、底图查询、校准矩阵保存 |
| 打点管理 | 4 | RTK 真值、校准采点、批量导入、历史查询 |
| 场地校准 | 2 | 校准模式判定、逐贝转换列表查询 |
| 健康检查 | 4 | 系统/MQTT/数据库/WebSocket 状态 |
| WebSocket | 1 | 实时遥测数据推送 |
| MQTT | 4 | 设备发现/状态/遥测/控制 |
| **合计** | **26** | |

Base URL: `http://localhost:8000`

所有 JSON 响应统一包装为 `ApiResponse`：

```json
{
  "code": 200,
  "message": "描述信息",
  "data": { ... }
}
```

---

## 1. 车辆管理 `/api/vehicle`

### 1.1 初始化新设备

`POST /api/vehicle/init`

将 MQTT discovery 上报的临时设备绑定为正式车辆，同时写入初始参数。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `temp_id` | string | 是 | 设备临时 ID，来自 `rtg/discovery` 消息 |
| `new_vehicle_id` | string | 是 | 分配的正式车辆编号，如 `RTG-001` |
| `label` | string | 否 | 显示名称，如 "RTG 01号机" |
| `params` | object | 否 | 车辆初始参数，结构见下方 |
| `params.vehicle_id` | string | 是(若有params) | 必须与 `new_vehicle_id` 一致 |
| `params.l_arm` | [float, float, float] | 否 | 杆臂向量 [dx, dy, dz]，单位 m |
| `params.height` | float | 否 | 吊具基准高度 H，单位 m |
| `params.w_span` | float | 否 | 门腿跨距 W_span，单位 m |
| `params.antennas` | object | 否 | 天线标识 `{"ant1": {"label": "大车左前"}, ...}` |

```json
{
  "temp_id": "RTG-NEW-8b36",
  "new_vehicle_id": "RTG-001",
  "label": "RTG 01号机",
  "params": {
    "vehicle_id": "RTG-001",
    "l_arm": [0.12, 0.08, -8.45],
    "height": 8.45,
    "w_span": 20.0,
    "antennas": {
      "ant1": {"label": "大车左前"},
      "ant2": {"label": "大车右前"},
      "ant3": {"label": "大车左后"},
      "ant4": {"label": "大车右后"},
      "ant5": {"label": "小车A端"},
      "ant6": {"label": "小车B端"}
    }
  }
}
```

**响应：**

```json
{"code": 200, "message": "设备初始化成功", "data": {"vehicle_id": "RTG-001"}}
```

**副作用：**
- `vehicles` 表：创建/更新记录，`initialized` 置为 `True`
- `vehicle_configs` 表：写入参数
- MQTT：向 `rtg/{temp_id}/control` 下发 `CMD_INIT_IDENTITY` 指令

---

### 1.2 获取车辆列表

`GET /api/vehicle/list`

返回所有车辆（含已初始化和待初始化），前端据此区分「已注册车辆」和「待初始化设备」。

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 数据库主键 |
| `vehicle_id` | string | 车辆编号（未初始化时等于 temp_id） |
| `temp_id` | string | 临时 ID |
| `sn` | string | 设备序列号（唯一标识物理设备） |
| `label` | string | 显示名称 |
| `is_initialized` | bool | 是否已完成初始化 |
| `status` | string | 在线状态（当前默认 "online"） |
| `height` | float\|null | 从 vehicle_configs 关联的标称高度 |
| `span` | float\|null | 从 vehicle_configs 关联的跨距 |
| `last_seen` | string\|null | 最后更新时间 ISO 格式 |

```json
{
  "code": 200,
  "message": "获取成功",
  "data": [
    {
      "id": 1,
      "vehicle_id": "RTG-001",
      "temp_id": "RTG-NEW-8b36",
      "sn": "SN-2024-001",
      "label": "RTG 01号机",
      "is_initialized": true,
      "status": "online",
      "height": 8.45,
      "span": 20.0,
      "last_seen": "2026-05-29T16:00:00+08:00"
    }
  ]
}
```

---

### 1.3 获取车辆详情

`GET /api/vehicle/{vehicle_id}`

**示例：** `GET /api/vehicle/RTG-001`

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `vehicle_id` | string | 车辆编号 |
| `initialized` | bool | 是否已初始化 |
| `config` | object\|null | 关联的参数配置 |
| `config.l_arm` | array | 杆臂向量 [dx, dy, dz] |
| `config.height` | float | 标称高度 |
| `config.w_span` | float | 门腿跨距 |
| `config.antennas` | object | 天线标识映射 |

```json
{
  "code": 200,
  "message": "获取成功",
  "data": {
    "vehicle_id": "RTG-001",
    "initialized": true,
    "config": {
      "l_arm": [0.12, 0.08, -8.45],
      "height": 8.45,
      "w_span": 20.0,
      "antennas": {"ant1": {"label": "大车左前"}, "ant2": {"label": "大车右前"}}
    }
  }
}
```

---

### 1.4 保存车辆参数

`POST /api/vehicle/config`

更新已注册车辆的参数。**要求车辆已存在于 `vehicles` 表**，否则返回 404。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `vehicle_id` | string | 是 | 目标车辆编号 |
| `l_arm` | [float, float, float] | 否 | 杆臂向量，天线相位中心相对 L-Point 的偏移 |
| `height` | float | 否 | 吊具基准高度 H (m) |
| `w_span` | float | 否 | 门腿跨距 W_span (m) |
| `antennas` | object | 否 | 天线标识 `{"ant_id": {"label": "名称"}}` |

```json
{
  "vehicle_id": "RTG-001",
  "l_arm": [0.12, 0.08, -8.45],
  "height": 8.45,
  "w_span": 20.0,
  "antennas": {
    "ant1": {"label": "大车左前"},
    "ant2": {"label": "大车右前"},
    "ant3": {"label": "大车左后"},
    "ant4": {"label": "大车右后"},
    "ant5": {"label": "小车A端"},
    "ant6": {"label": "小车B端"}
  }
}
```

**响应：** `{"code": 200, "message": "配置保存成功", "data": {"vehicle_id": "RTG-001"}}`

---

### 1.5 克隆车辆配置

`POST /api/vehicle/config/clone`

复制源车辆的全部参数到目标车辆，用于同型号 RTG 快速部署。若目标车辆不存在，自动创建。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `source_vehicle_id` | string | 是 | 源车辆编号（必须已有配置） |
| `new_vehicle_id` | string | 是 | 目标车辆编号 |

```json
{"source_vehicle_id": "RTG-001", "new_vehicle_id": "RTG-002"}
```

**响应：** `{"code": 200, "message": "配置复制成功", "data": {"source_vehicle_id": "RTG-001", "new_vehicle_id": "RTG-002"}}`

---

### 1.6 获取车辆参数

`GET /api/vehicle/config/{vehicle_id}`

**示例：** `GET /api/vehicle/config/RTG-001`

返回该车辆的完整参数配置，若无配置则 `data` 为 `null`。

```json
{
  "code": 200,
  "message": "获取成功",
  "data": {
    "vehicle_id": "RTG-001",
    "l_arm": [0.12, 0.08, -8.45],
    "height": 8.45,
    "w_span": 20.0,
    "antennas": {"ant1": {"label": "大车左前"}},
    "created_at": "2026-05-29T15:28:10",
    "updated_at": "2026-05-29T15:35:10"
  }
}
```

---

## 2. 堆场管理 `/api/yard`

### 2.1 获取所有堆场

`GET /api/yard/all_origins`

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `yard_id` | string | 堆场唯一标识 |
| `name` | string | 堆场名称 |
| `origin` | [float, float, float] | 原点坐标 [lon, lat, alt]（GeoJSON 顺序） |
| `heading` | float | 堆场朝向角 (度) |
| `bay_spacing` | float | 贝位间距 (m) |
| `total_bays` | int | 贝位总数 |
| `mapped` | bool | 是否已完成 A 车建图 |

```json
{
  "code": 200,
  "data": [
    {
      "yard_id": "yard_01",
      "name": "A区堆场",
      "origin": [120.654321, 30.123456, 5.20],
      "heading": 87.5,
      "bay_spacing": 6.0,
      "total_bays": 40,
      "mapped": false
    }
  ]
}
```

---

### 2.2 创建/更新堆场

`POST /api/yard/create`

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `yard_id` | string | 是 | 堆场唯一标识 |
| `name` | string | 是 | 堆场名称 |
| `origin_lat` | float | 是 | 原点纬度 (WGS84) |
| `origin_lon` | float | 是 | 原点经度 (WGS84) |
| `origin_alt` | float | 是 | 原点高程 (m) |
| `heading_deg` | float | 否 | 堆场朝向角 (度)，默认 0 |
| `bay_spacing` | float | 否 | 贝位间距 (m)，默认 0（实际间距不固定时可不填） |
| `bay_count` | int | 是 | 贝位总数（估算值） |

```json
{
  "yard_id": "yard_01",
  "name": "A区堆场",
  "origin_lat": 30.123456,
  "origin_lon": 120.654321,
  "origin_alt": 5.0,
  "bay_count": 40
}
```

---

### 2.3 获取堆场底图 / 校准上下文（聚合查询）

`GET /api/yard/map?yard_id={yard_id}&vehicle_id={vehicle_id}`

**用途：** 校准页、作业页一次性拉齐「场地 RTK 真值 + 本车校准进度 + 逐贝对比结果」。  
**不是**单独下载 GIS 底图文件；名称沿用历史「底图」说法。

| 参数 | 必填 | 说明 |
|------|------|------|
| `yard_id` | 是 | 堆场 ID，如 `yard_01` |
| `vehicle_id` | 否 | **车辆相关字段仅在此参数存在时返回**；不传则只返回场地侧 RTK |

**未传 `vehicle_id` 时（仅场地维度）：**

| 字段 | 值 |
|------|-----|
| `vehicle_id` | `null` |
| `calibration_status` | `"none"` |
| `calibration_mode` | `"none"` |
| `m_matrix` | `null` |
| `transform_list` | `[]` |
| `has_rtk_map` / `survey_points` | 正常返回 |

**传了 `vehicle_id` 时（场地 + 本车校准）：** 上表车辆字段按 `calibration_data` / `vehicle_bay_transforms` 填充。

**响应字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `yard_id` | string | 堆场标识 |
| `vehicle_id` | string\|null | 请求中的车辆 ID（未传则为 null） |
| `calibration_status` | string | 本车在该场的校准记录；**未传 `vehicle_id` 恒为 `none`** |
| `calibration_mode` | string | 业务校准模式；**未传 `vehicle_id` 恒为 `none`** |
| `has_rtk_map` | bool | 是否存在 RTK 场地真值（≥1 个 `mapping` 点） |
| `rtk_point_count` | int | RTK 贝位数量 |
| `survey_points` | array | **仅** 手持 RTK 录入的真值（`point_type=mapping`），供校准对比基准 |
| `transform_list` | array | 本车逐贝校准记录（`vehicle_bay_transforms`），每项含 `ref`（RTK）/ `measured`（车载）/ `delta`；**需传 `vehicle_id`** |
| `m_matrix` | object\|null | 本车校准完成快照；**未传 `vehicle_id` 恒为 `null`**（不会返回他车数据） |
| `source_vehicle_id` | string\|null | 校准2 参考车 ID（如 RTG-001）；校准1 多为 null |

**`calibration_mode` 判定逻辑（传了 `vehicle_id` 时）：**

1. 本车 `calibration_status === completed` → `skip`（校准3，无需再采）
2. 否则若同场已有**其他车** `completed` → `verify`（校准2，只采前 3 个 RTK 贝位）
3. 否则 → `full`（校准1，需覆盖全部 RTK 贝位）

**响应示例（与你当前数据一致）：**

```json
{
  "code": 200,
  "message": "获取成功",
  "data": {
    "yard_id": "yard_01",
    "vehicle_id": "RTG-001",
    "calibration_status": "completed",
    "calibration_mode": "skip",
    "has_rtk_map": true,
    "rtk_point_count": 2,
    "m_matrix": {
      "mode": "full",
      "steps": 2,
      "transform_list": [ "... 完成校准时写入的快照 ..." ]
    },
    "transform_list": [
      {
        "bay_no": 1,
        "ref": { "lat": 30.1234567, "lon": 120.6543209, "alt": 0 },
        "measured": { "lat": 30.123456, "lon": 120.654321, "alt": 5.2, "local_x": 12.5, "local_y": 3.8, "local_z": 5.2 },
        "delta": { "lat": -7e-7, "lon": 1e-7, "alt": 5.2 }
      }
    ],
    "survey_points": [
      { "id": 1, "point_type": "mapping", "bay_no": 1, "lat": 30.1234567, "lon": 120.6543209, "alt": 0 }
    ],
    "source_vehicle_id": null
  }
}
```

**字段对照（你这份响应的含义）：**

| 你看到的值 | 含义 |
|------------|------|
| `calibration_status: completed` | `RTG-001` 在 `yard_01` 已完成校准（`calibration_data` 表） |
| `calibration_mode: skip` | 校准3：该车不必再逐贝采点 |
| `rtk_point_count: 2` | 建图页录入了 2 个 RTK 贝位（贝位 1、2） |
| `survey_points` | 上述 2 个 RTK 真值坐标 |
| `transform_list` | `RTG-001` 校准时各贝位的 RTK vs 车载解算对比（实时查表，含 `local_x/y/z`） |
| `m_matrix.transform_list` | 调用 `POST /api/survey/finalize` 时写入的**历史快照**，可能与顶层 `transform_list` 结构略不同 |
| `source_vehicle_id` | 校准2 完成后为参考车；校准1 为 null |

**与专用接口的关系：**

| 本接口字段 | 也可单独查 |
|------------|------------|
| `calibration_mode` | `GET /api/calibration/mode?vehicle_id=&yard_id=` |
| `transform_list` | `GET /api/calibration/transform_list?vehicle_id=&yard_id=` |
| `survey_points` | `GET /api/survey/history?yard_id=&point_type=mapping` |

前端校准页优先调本接口以减少请求次数；`history` 作为 map 失败时的回退。

---

### 2.4 生成底图（占位）

`POST /api/yard/generate_map`

当前为占位接口，实际建图通过 `POST /api/survey/confirm (mode=mapping)` 逐点完成。

```json
{"yard_id": "yard_01", "vehicle_id": "RTG-001"}
```

---

### 2.5 保存校准矩阵

`POST /api/yard/save_map`

保存 A 车建图后的转换矩阵，并将堆场标记为「已建图」。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `yard_id` | string | 是 | 堆场标识 |
| `vehicle_id` | string | 是 | 建图车辆编号 |
| `m_matrix` | object | 是 | 转换矩阵，如 `{"R": [[1,0,0],[0,1,0],[0,0,1]], "t": [0.01,-0.02,0.05]}` |

**副作用：**
- `calibration_data` 表：保存 m_matrix，status 置为 "completed"
- `yards` 表：mapped 置为 True

---

## 3. 打点管理 `/api/survey`

### 3.1 确认打点

`POST /api/survey/confirm`

**`mode=mapping`（RTK 真值）：** 仅允许 `vehicle_id=RTK_HANDHELD`，写入 `survey_points`（同 `bay_no` 覆盖）。有 RTK 点后 `yards.mapped=true`。

**`mode=calibration`（车载对比）：** 读取同贝位 RTK 真值，写入 `vehicle_bay_transforms`（每车每场地独立列表，同贝位覆盖）。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `vehicle_id` | string | 是 | mapping 固定 `RTK_HANDHELD`；calibration 为 RTG 编号 |
| `yard_id` | string | 是 | 堆场标识 |
| `bay_id` | string | 是 | 贝位号，如 `"1"` / `"Bay_01"`（解析为同一 bay_no） |
| `mode` | string | 否 | `mapping` / `calibration` |
| `lat` / `lon` / `alt` | float | 否 | WGS84 坐标 |
| `local_x/y/z` | float | 否 | 校准模式可选，车载解算本地坐标 |

**RTK 示例：**

```json
{
  "vehicle_id": "RTK_HANDHELD",
  "yard_id": "yard_01",
  "bay_id": "1",
  "mode": "mapping",
  "lat": 30.123456,
  "lon": 120.654321,
  "alt": 5.2
}
```

**校准采点示例：**

```json
{
  "vehicle_id": "RTG-001",
  "yard_id": "yard_01",
  "bay_id": "Bay_03",
  "mode": "calibration",
  "lat": 30.123460,
  "lon": 120.654325,
  "alt": 5.18
}
```

**响应：**

```json
{
  "code": 200,
  "message": "打点确认成功",
  "data": {
    "vehicle_id": "RTG-001",
    "yard_id": "yard_01",
    "bay_id": "Bay_01",
    "bay_no": 1,
    "survey_id": 42
  }
}
```

---

### 3.2 完成校准

`POST /api/survey/finalize`

在 **`confirm(mode=calibration)` 已写入足够逐贝记录** 后调用，标记该车在该场地的校准状态为 `completed`。  
**不会**根据请求体里的 `steps` 写入采点数据；`transform_list` 快照一律从数据库 `vehicle_bay_transforms` 读取。

**前置校验（不满足返回 400）：**

| 条件 | 说明 |
|------|------|
| 堆场存在 | `yard_id` 在 `yards` 表中 |
| 有 RTK 真值 | 该场至少 1 个 `mapping` 点 |
| 有校准采点 | 该车在该场至少 1 条 `vehicle_bay_transforms` |
| 采点贝位合法 | 每条 transform 的 `bay_no` 必须有对应 RTK |
| 数量达标 | 校准1：已采贝位 = 全部 RTK 贝位；校准2：至少 `min(3, RTK数)` 个 |

仅传 `calibration_data.steps` 而无实际 `confirm` 记录时，将返回 **400**。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `vehicle_id` | string | 是 | 车辆编号 |
| `yard_id` | string | 是 | 堆场标识 |
| `calibration_data` | object | 否 | 校准数据（如 SVD 计算结果、各步骤偏差等） |

```json
{
  "vehicle_id": "RTG-001",
  "yard_id": "yard_01",
  "calibration_data": {
    "steps": [
      {"step": 1, "bay_id": "Bay_01", "delta": {"x": 0.012, "y": -0.008, "z": 0.003}},
      {"step": 2, "bay_id": "Bay_20", "delta": {"x": 0.015, "y": -0.010, "z": 0.002}},
      {"step": 3, "bay_id": "Bay_40", "delta": {"x": 0.010, "y": -0.006, "z": 0.004}}
    ]
  }
}
```

**响应：** `{"code": 200, "message": "校准完成", "data": {"vehicle_id": "RTG-001", "yard_id": "yard_01", "calibration_id": 1, "status": "completed"}}`

---

### 3.3 查询打点历史

`GET /api/survey/history?yard_id={yard_id}&point_type={type}&vehicle_id={id}`

| 参数 | 必填 | 说明 |
|------|------|------|
| `yard_id` | 是 | 堆场 ID |
| `point_type` | 否 | `mapping`：RTK 真值（`survey_points`）；`calibration`：车载校准采点（`vehicle_bay_transforms`） |
| `vehicle_id` | 条件 | **`point_type=calibration` 时必填**；`mapping` 时忽略（RTK 与车无关） |

**数据来源：**

| `point_type` | 数据表 | `vehicle_id` |
|--------------|--------|----------------|
| `mapping`（建图页常用） | `survey_points` | 不传 |
| `calibration` | `vehicle_bay_transforms` | 必传，如 `RTG-001` |
| 未传 `point_type` | `survey_points` 该场全部类型 | 忽略 |

车载校准列表也可直接用：`GET /api/calibration/transform_list?vehicle_id=&yard_id=`（字段更全）。

**`point_type=mapping` 响应示例：**

```json
{
  "code": 200,
  "data": [
    {
      "id": 1,
      "point_type": "mapping",
      "bay_no": 1,
      "bay_id": "Bay_01",
      "lat": 30.1234567,
      "lon": 120.6543209,
      "alt": 0.0,
      "local_x": null,
      "local_y": null,
      "local_z": null,
      "created_at": "2026-06-02T17:05:51+08:00"
    }
  ]
}
```

**`point_type=calibration` 响应额外字段：**

| 字段 | 说明 |
|------|------|
| `vehicle_id` | 车辆编号 |
| `ref` | 同贝位 RTK 真值 |
| `delta` | 车载 − RTK（WGS84） |

---

### 3.4 批量导入打点坐标

`POST /api/survey/batch_import`

一次性导入多个贝位的 GPS 坐标，适用于 RTK 手持设备批量采集后整理导入，或从 CSV 文件批量上传。

**请求字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `yard_id` | string | 是 | 堆场标识 |
| `mode` | string | 否 | `"mapping"`（默认） |
| `points` | array | 是 | 贝位坐标列表，至少 1 条 |
| `points[].bay_id` | string | 是 | 贝位标识，如 "Bay_01" |
| `points[].lat` | float | 是 | 纬度 (WGS84) |
| `points[].lon` | float | 是 | 经度 (WGS84) |
| `points[].alt` | float | 否 | 高程 (m)，默认 0 |

```json
{
  "yard_id": "yard_01",
  "mode": "mapping",
  "points": [
    {"bay_id": "Bay_01", "lat": 30.1234567, "lon": 120.6543210, "alt": 5.20},
    {"bay_id": "Bay_02", "lat": 30.1234600, "lon": 120.6543250, "alt": 5.15},
    {"bay_id": "Bay_03", "lat": 30.1234630, "lon": 120.6543290, "alt": 5.18}
  ]
}
```

**响应：**

```json
{
  "code": 200,
  "message": "成功导入 3 个贝位坐标",
  "data": {
    "yard_id": "yard_01",
    "count": 3,
    "points": [
      {"bay_id": "Bay_01", "bay_no": 1},
      {"bay_id": "Bay_02", "bay_no": 2},
      {"bay_id": "Bay_03", "bay_no": 3}
    ]
  }
}
```

**前端 CSV 文件格式：**

```csv
# RTK 手持设备采集数据 —— 每行一个贝位
# bay_id, lat, lon, alt
Bay_01, 30.1234567, 120.6543210, 5.20
Bay_02, 30.1234600, 120.6543250, 5.15
Bay_03, 30.1234630, 120.6543290, 5.18
```

支持 `#` 开头注释行、表头行自动跳过，分隔符支持逗号、制表符、分号。

---

## 4. 场地校准 `/api/calibration`

### 4.1 查询校准模式

`GET /api/calibration/mode?vehicle_id={id}&yard_id={id}`

| `calibration_mode` | 含义 |
|------------------|------|
| `full` | 校准1：场地首车，需全场逐贝对比 |
| `verify` | 校准2：其它车已校准，本车仅前 3 贝核验 |
| `skip` | 校准3：本车已完成，无需再校准 |

```json
{
  "code": 200,
  "data": {
    "vehicle_id": "RTG-002",
    "yard_id": "yard_01",
    "calibration_mode": "verify",
    "rtk_point_count": 40,
    "reference_vehicle_id": "RTG-001"
  }
}
```

### 4.2 获取逐贝转换列表

`GET /api/calibration/transform_list?vehicle_id={id}&yard_id={id}`

每车在每个场地有独立的转换列表（表 `vehicle_bay_transforms`）。

```json
{
  "code": 200,
  "data": {
    "vehicle_id": "RTG-001",
    "yard_id": "yard_01",
    "count": 40,
    "transform_list": [
      {
        "bay_no": 1,
        "bay_id": "Bay_01",
        "ref": {"lat": 30.123456, "lon": 120.654321, "alt": 5.2},
        "measured": {"lat": 30.123458, "lon": 120.654323, "alt": 5.18, "local_x": 12.5, "local_y": 3.8, "local_z": 5.18},
        "delta": {"lat": 0.000002, "lon": 0.000002, "alt": -0.02}
      }
    ]
  }
}
```

### 4.3 清空逐贝校准记录

`DELETE /api/calibration/transform_list?vehicle_id={id}&yard_id={id}`

清空本车在本场的 `vehicle_bay_transforms`，并将 `calibration_data.status` 置为 `pending`、`m_matrix` 置空。  
单贝覆盖/追加请用 `POST /api/survey/confirm (mode=calibration)`，无需先清空。

---

## 5. 健康检查

### 5.1 综合健康检查

`GET /api/health`

```json
{
  "code": 200,
  "data": {
    "status": "healthy",
    "timestamp": "2026-05-29T16:00:00",
    "mqtt_connected": true,
    "db_connected": true,
    "version": "1.0.0"
  }
}
```

### 5.2 MQTT 连接状态

`GET /api/health/mqtt`

```json
{
  "code": 200,
  "data": {
    "connected": true,
    "broker_host": "localhost",
    "broker_port": 1883,
    "subscribed_topics": ["rtg/+/telemetry", "rtg/+/status", "rtg/discovery"]
  }
}
```

### 5.3 数据库状态

`GET /api/health/db`

```json
{
  "code": 200,
  "data": {
    "connected": true,
    "database": "sqlite:///./data/ground_truth.db",
    "tables": ["vehicles", "yards", "survey_points", "vehicle_configs", "calibration_data", "vehicle_bay_transforms"]
  }
}
```

### 5.4 WebSocket 连接统计

`GET /api/status/websocket`

```json
{
  "code": 200,
  "data": {
    "total_connections": 3,
    "connections_by_vehicle": {"RTG-001": 2, "RTG-002": 1}
  }
}
```

---

## 6. WebSocket 实时数据

### 连接

```
ws://localhost:8000/ws/realtime?vehicle_id=RTG-001
```

前端通过 WebSocket 订阅指定车辆的实时数据。后端根据 `vehicle_id` 从 MQTT Broker 获取对应车辆的 telemetry 并转发。

### 消息类型

后端会推送多种类型的消息，前端需根据 `type` 字段区分处理：

**telemetry（遥测数据）：**

```json
{
  "type": "telemetry",
  "data": {
    "timestamp": "2026-05-29T16:00:00.123+08:00",
    "gps_coord": {"lat": 30.123456, "lon": 120.654321, "alt": 5.2},
    "local_coord": {"x": 12.5, "y": 3.8, "z": 5.2},
    "heading": 87.3,
    "roll": 0.12,
    "pitch": -0.05,
    "speed": 0.0,
    "status": "fix",
    "receivers": [
      {"id": "ant1", "lat": 30.123456, "lon": 120.654321, "alt": 5.2, "fix_type": "fix"}
    ],
    "gantry": { ... },
    "trolley": { ... }
  }
}
```

**discovery（新设备上线，广播给所有连接）：**

```json
{
  "type": "discovery",
  "data": {"temp_id": "RTG-NEW-8b376", "sn": "SN-2024-001"}
}
```

**status（设备状态变更）：**

```json
{
  "type": "status",
  "data": {"event": "online", "uptime": 120, "signal_quality": "good"}
}
```

---

## 7. MQTT Topic 约定

| Topic | 方向 | 说明 |
|-------|------|------|
| `rtg/discovery` | 中控机 -> 后端 | 设备首次上线广播 |
| `rtg/{vehicle_id}/status` | 中控机 -> 后端 | 设备状态心跳 |
| `rtg/{vehicle_id}/telemetry` | 中控机 -> 后端 | 实时定位数据 |
| `rtg/{vehicle_id}/control` | 后端 -> 中控机 | 下发控制指令 |

### discovery 消息格式

```json
{"temp_id": "RTG-NEW-8b376", "sn": "SN-2024-001"}
```

| 字段 | 说明 |
|------|------|
| `temp_id` | 中控机自动生成的临时 ID，格式 `RTG-NEW-{随机hex}` |
| `sn` | 设备序列号，物理设备唯一标识，出厂烧录 |

### control 指令格式（init 示例）

```json
{
  "cmd": "CMD_INIT_IDENTITY",
  "data": {
    "new_vehicle_id": "RTG-001",
    "label": "RTG 01号机"
  }
}
```

---

## 8. 数据库表结构

| 表名 | 用途 | 关键字段 |
|------|------|----------|
| `vehicles` | 车辆注册 | `vehicle_id`, `sn`, `initialized` |
| `vehicle_configs` | 步骤② 静态参数 | `l_arm`, `height`, `w_span`, `antennas` |
| `yards` | 堆场元信息 | `origin_*`, `mapped`（有 RTK 真值即为 true） |
| `survey_points` | 步骤① RTK 真值 | `point_type=mapping`, `bay_no`, `lat/lon/alt` |
| `vehicle_bay_transforms` | 步骤③ 逐贝转换列表 | `(vehicle_id, yard_id, bay_no)` 唯一，`ref_*`, `meas_*`, `delta_*` |
| `calibration_data` | 每车每场地校准快照 | `vehicle_id`, `yard_id`, `m_matrix`, `status`, `source_vehicle_id` |

**残差说明**：逐贝平面/高程残差统一存于 `vehicle_bay_transforms` 的 `delta_lat` / `delta_lon` / `delta_alt`（`delta = measured − ref`）。作业时中控经 API 读取并贝间插值，不再使用已废弃的 `z_patches` 表。

---

## 9. 完整业务流程

```
0. 设备上线
   rtg/discovery -> POST /api/vehicle/init -> 步骤② 配置 vehicle_configs

步骤① RTK 场地真值（Mapping 页）
   POST /api/survey/confirm (RTK_HANDHELD, mode=mapping)
   或 POST /api/survey/batch_import
   -> survey_points；yards.mapped=true

步骤② RTG 静态测量（VehicleConfig 页）
   POST /api/vehicle/config

步骤③ 场地校准（Calibration 页）
   校准1 full：全场 RTK 贝位逐点 confirm(calibration)
        -> vehicle_bay_transforms（RTG-001 @ yard_01 独立列表）
   校准2 verify：换车 RTG-002，仅前 3 贝核验（可参考 RTG-001 模型）
   校准3 skip：同车再来，calibration_status=completed，直接作业
   POST /api/survey/finalize -> calibration_data（M + transform_list）

步骤④ 作业
   ws://.../ws/realtime?vehicle_id=RTG-001
```
