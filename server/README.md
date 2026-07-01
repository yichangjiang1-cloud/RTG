# RTG 后端服务

RTG 高精度定位系统的 **Web 后端**：连接 **中控机（MQTT）**、**React 前端（REST + WebSocket）** 与 **SQLite 真值库**。负责设备注册、场地/车辆配置、RTK 建图、车辆校准数据持久化，以及实时遥测转发。

完整接口说明见：[API_INTERFACES.md](./API_INTERFACES.md)

---

## 技术栈

| 层次 | 技术 | 用途 |
|------|------|------|
| Web 框架 | [FastAPI](https://fastapi.tiangolo.com/) | REST API、WebSocket、生命周期管理 |
| ASGI 服务 | [Uvicorn](https://www.uvicorn.org/) | 开发与生产运行 |
| ORM | [SQLAlchemy 2.x](https://www.sqlalchemy.org/) | SQLite 表映射与查询 |
| 校验模型 | [Pydantic v2](https://docs.pydantic.dev/) + pydantic-settings | 请求/响应结构与配置 |
| 消息总线 | [Paho MQTT](https://www.eclipse.org/paho/) | 订阅中控遥测、发布控制指令 |
| 数据库 | SQLite（`data/ground_truth.db`） | 车辆、场地、RTK、校准数据 |
| 可选部署 | Docker Compose + Eclipse Mosquitto | 一键起后端与 Broker |

---

## 架构与思路

### 角色分工

```
  中控机 (src/)          后端 (server/)           前端 (fronted/)
       │                      │                        │
       │  MQTT telemetry      │  REST 配置/建图/校准    │
       ├─────────────────────►│◄───────────────────────┤
       │  discovery/status    │  WebSocket 实时推送     │
       │                      ├───────────────────────►│
       │◄──── control ────────┤                        │
```

- **中控机**：GNSS/IMU 融合解算，经 MQTT 上报定位与设备发现。
- **本后端**：不跑解算；做 **数据枢纽**（入库、校验、聚合查询）和 **实时转发**（MQTT → WebSocket）。
- **前端**：场地建图、车辆配置、校准、作业监控；通过 HTTP 写库，通过 WS 看实时坐标。

### 业务流程（与库表对应）

| 步骤 | 业务 | 主要 API | 数据表 |
|------|------|----------|--------|
| 0 | 设备上线 / 初始化 | `POST /api/vehicle/init` | `vehicles`, `vehicle_configs` |
| ① | RTK 场地真值（建图） | `POST /api/survey/confirm` (mapping) | `survey_points`, `yards.mapped` |
| ② | RTG 静态参数 | `POST /api/vehicle/config` | `vehicle_configs` |
| ③ | 车辆校准（逐贝对比） | `confirm` (calibration) → `finalize` | `vehicle_bay_transforms`, `calibration_data` |
| 作业 | 读校准态 / 实时定位 | `GET /api/yard/map`, `WS /ws/realtime` | 聚合查询 + WS |

**校准模式**（`GET /api/calibration/mode`）：

- **full（校准1）**：本场尚无他车完成 → 需覆盖全部 RTK 贝位。
- **verify（校准2）**：他车已完成 → 本车至少核验 3 个贝位。
- **skip（校准3）**：本车已 `completed` → 可继续改贝位，需重新 `finalize`。

### 设计要点

1. **统一响应**：`ApiResponse { code, message, data }`（见 `schemas.py`）。
2. **RTK 与车载分离**：RTK 真值在 `survey_points`（`point_type=mapping`）；车载对比在 `vehicle_bay_transforms`。
3. **同贝位覆盖**：`yard_id + bay_no`（RTK）或 `vehicle_id + yard_id + bay_no`（校准）upsert，支持建图/校准页「可改可追加」。
4. **MQTT 线程桥接**：Paho 回调在子线程，`asyncio.run_coroutine_threadsafe` 转入 FastAPI 事件循环处理（见 `main.py`）。

---

## 项目结构

```
server/
├── app/
│   ├── main.py              # 应用入口、路由注册、WebSocket、生命周期
│   ├── config.py            # Settings（环境变量 / .env）
│   ├── database.py          # 引擎、Session、init_db、SQLite 迁移
│   ├── models.py            # SQLAlchemy ORM 表定义
│   ├── schemas.py           # Pydantic 请求/响应模型
│   ├── mqtt/
│   │   ├── client.py        # MQTT 连接、订阅、发布
│   │   └── handlers.py      # 主题分发 → DB / WebSocket
│   ├── api/
│   │   ├── ws.py            # WebSocketManager
│   │   ├── deps.py          # 依赖注入（数据库会话）
│   │   └── routers/
│   │       ├── vehicle.py   # 车辆注册与参数
│   │       ├── yard.py      # 堆场与聚合底图
│   │       ├── survey.py    # RTK / 校准采点
│   │       ├── calibration.py # 校准模式与转换列表
│   │       └── health.py    # 健康检查
│   └── utils/
│       ├── logger.py        # 日志
│       └── constants.py     # 常量
├── data/                    # SQLite（运行时生成）
├── logs/                    # 后端日志
├── mosquitto/               # Broker 配置
├── API_INTERFACES.md        # 接口文档（权威）
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── run.bat / run.sh
└── test_api.py              # 手工联调脚本
```

---

## 核心模块说明

### `app/main.py`

- 创建 FastAPI 应用，注册 CORS。
- **lifespan**：启动时 `init_db()`、创建 `WebSocketManager`、连接 MQTT 并绑定 `MqttMessageHandler`。
- 注册路由：`vehicle`, `yard`, `survey`, `calibration`, `health`。
- 暴露 `GET /`、`GET /api/version`、`WS /ws/realtime?vehicle_id=`。

### `app/config.py` — `Settings`

集中配置：服务端口、`DATABASE_URL`、MQTT Broker、日志级别等；支持 `.env` 与环境变量覆盖。

### `app/database.py`

- `engine` / `SessionLocal` / `get_db()`：请求级数据库会话。
- `init_db()`：`Base.metadata.create_all` 建表。
- `_sqlite_migrate()`：为旧库补齐新增列（如 `calibration_data.source_vehicle_id`）。

### `app/models.py` — ORM 表

| 类 | 表名 | 作用 |
|----|------|------|
| `Vehicle` | `vehicles` | 车辆注册：`vehicle_id`、`sn`、是否已初始化 |
| `Yard` | `yards` | 堆场元数据：原点、朝向、贝位数、`mapped`（有 RTK 即为 true） |
| `SurveyPoint` | `survey_points` | **RTK 真值**（`point_type=mapping` 等），按 `yard_id + bay_no` 覆盖 |
| `VehicleConfig` | `vehicle_configs` | 杆臂、跨距、天线角色等静态参数 |
| `VehicleBayTransform` | `vehicle_bay_transforms` | **逐贝校准**：RTK ref vs 车载 meas、`delta_*` |
| `CalibrationData` | `calibration_data` | 每车每场地一条：`status`、`m_matrix`（finalize 快照） |

### `app/schemas.py`

- 各模块请求体：`SurveyConfirmRequest`、`SurveyFinalizeRequest`、`YardCreateRequest` 等。
- 统一包装：`ApiResponse(code, message, data)`。

### `app/mqtt/client.py` — `MqttClient`

- 连接 Broker，订阅 `rtg/+/telemetry`、`rtg/+/status`、`rtg/discovery`。
- `publish_control(vehicle_id, payload)`：向 `rtg/{id}/control` 下发指令（如初始化身份）。

### `app/mqtt/handlers.py` — `MqttMessageHandler`

| 主题 | 行为 |
|------|------|
| `telemetry` | 解析 `vehicle_id`，`ws_manager.broadcast_to_vehicle` 推前端 |
| `status` | 状态事件同样转发 WS |
| `discovery` | 新设备写入/更新 `vehicles`，广播 `type=discovery` 给所有 WS 客户端 |

### `app/api/ws.py` — `WebSocketManager`

- 按 `vehicle_id` 维护连接集合。
- `broadcast_to_vehicle` / `broadcast_all`：MQTT 数据到前端的桥梁。

### `app/api/routers/vehicle.py`

- `init`：绑定 `temp_id` → `vehicle_id`，写配置，MQTT 下发 `CMD_INIT_IDENTITY`。
- `list` / `get`：车辆列表与详情。
- `config` 保存/读取/克隆：步骤② 静态测量。

### `app/api/routers/yard.py`

- `all_origins`：堆场列表（有 RTK 时同步 `mapped=true`）。
- `create`：创建/更新堆场。
- **`map`**：聚合查询（RTK + 本车 `transform_list` + `calibration_*`）；**未传 `vehicle_id` 时不返回车辆校准字段**。
- `generate_map` / `save_map`：占位或保存矩阵（部分场景与 `finalize` 重叠）。

### `app/api/routers/survey.py`

- **`confirm`**：`mode=mapping` → RTK 真值；`mode=calibration` → `vehicle_bay_transforms`（同贝覆盖）。
- **`finalize`**：校验 RTK/采点数量后，`calibration_data.status=completed`，写入 `m_matrix` 快照。
- **`history`**：`point_type=mapping` 查 RTK；`point_type=calibration` 需 `vehicle_id`。
- **`batch_import`**：CSV 批量 RTK。
- **`DELETE rtk_points`**：清空某场 RTK。

### `app/api/routers/calibration.py`

- **`mode`**：返回 `full` / `verify` / `skip`。
- **`transform_list`**：逐贝转换列表（与 `yard/map` 中顶层列表同源）。
- **`DELETE transform_list`**：清空本车本场校准，重置为 `pending`。

### `app/api/routers/health.py`

- 综合健康、MQTT、数据库、WebSocket 连接统计。

### `app/api/deps.py`

- `get_database()`：对 `get_db` 的薄封装（多数路由直接用 `Depends(get_db)`）。

### `app/utils/logger.py`

- `setup_logging()` / `get_logger()`：控制台 + `logs/backend.log`。

---

## 快速开始

### Docker Compose（推荐）

```bash
cd server
docker-compose up -d
docker-compose logs -f
```

访问：http://localhost:8000  
Swagger：http://localhost:8000/docs

### 本地开发

```bash
cd server
pip install -r requirements.txt

# MQTT（示例：Docker）
docker run -d -p 1883:1883 -p 9001:9001 eclipse-mosquitto:latest

# 启动后端（在 server 目录下执行，保证 data/ 路径正确）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Windows 也可使用 `run.bat`（Docker 编排）。

---

## 配置

环境变量或 `server/.env`：

```env
MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
DATABASE_URL=sqlite:///./data/ground_truth.db
LOG_LEVEL=INFO
DEBUG=false
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
```

数据库文件默认：`server/data/ground_truth.db`（相对启动工作目录）。

---

## API 一览

| 模块 | 前缀 | 说明 |
|------|------|------|
| 车辆 | `/api/vehicle` | 初始化、列表、参数、克隆 |
| 堆场 | `/api/yard` | 列表、创建、**map 聚合** |
| 打点 | `/api/survey` | confirm、finalize、history、批量导入、清空 RTK |
| 校准 | `/api/calibration` | mode、transform_list、清空校准 |
| 健康 | `/api/health` | 系统 / MQTT / DB / WS |

**WebSocket**：`ws://localhost:8000/ws/realtime?vehicle_id=RTG-001`

消息类型：`telemetry`、`status`、`discovery`（与 MQTT 一致）。

---

## 典型使用说明

### 1. 前端对接

```typescript
const backendConfig = { host: 'localhost', port: 8000 };
// REST: http://localhost:8000/api/...
// WS:   ws://localhost:8000/ws/realtime?vehicle_id=RTG-001
```

### 2. 场地建图（RTK）

```http
POST /api/survey/confirm
{ "vehicle_id": "RTK_HANDHELD", "yard_id": "yard_01", "bay_id": "1", "mode": "mapping", "lat": 30.12, "lon": 120.65, "alt": 0 }
```

查询：`GET /api/survey/history?yard_id=yard_01&point_type=mapping`

### 3. 车辆校准

```http
# 单贝采点（可反复覆盖）
POST /api/survey/confirm
{ "vehicle_id": "RTG-001", "yard_id": "yard_01", "bay_id": "1", "mode": "calibration", "lat": ..., "lon": ..., "alt": ... }

# 标记完成（有 RTK + 足够采点才成功）
POST /api/survey/finalize
{ "vehicle_id": "RTG-001", "yard_id": "yard_01", "calibration_data": { "mode": "full" } }
```

查询：`GET /api/calibration/transform_list?vehicle_id=RTG-001&yard_id=yard_01`  
聚合：`GET /api/yard/map?yard_id=yard_01&vehicle_id=RTG-001`

> **注意**：`yard_id` 必须与建图一致（如 `yard_01`，不是 `yard-01`）。

### 4. 手工测试

```bash
cd server
python test_api.py

curl http://localhost:8000/api/health
curl "http://localhost:8000/api/yard/map?yard_id=yard_01&vehicle_id=RTG-001"
```

### 5. MQTT 主题

| 方向 | 主题 | 说明 |
|------|------|------|
| 订阅 | `rtg/+/telemetry` | 实时定位 |
| 订阅 | `rtg/+/status` | 状态 |
| 订阅 | `rtg/discovery` | 新设备 |
| 发布 | `rtg/{vehicle_id}/control` | 控制指令 |

---

## 常见问题

### MQTT 连接失败

确认 Broker 已启动，`MQTT_BROKER_HOST` / `PORT` 正确；后端可启动但 WS 无遥测。

### `yard/map` 与 `transform_list` 不一致

检查 **`yard_id` 拼写完全一致**（下划线/大小写）。`map` 在未传 `vehicle_id` 时不会返回车辆校准数据。

### `finalize` 返回 400

需满足：本场有 RTK、本车有 `confirm(calibration)` 记录、数量达到 full/verify 要求。仅传 `calibration_data.steps` 不会写入采点。

### SQLite database is locked

关闭多余后端进程，必要时删除 `data/ground_truth.db-wal` / `-shm` 后重启。

---

## 扩展开发

1. **新 API**：在 `app/api/routers/` 增加模块，在 `main.py` 中 `include_router`。
2. **新表**：在 `models.py` 定义模型，必要时在 `database._sqlite_migrate()` 增加 ALTER。
3. **新 MQTT 逻辑**：改 `handlers.py`，保持异步通过 `run_coroutine_threadsafe` 调度。

---

## 相关文档

- [API_INTERFACES.md](./API_INTERFACES.md) — 接口字段、示例、业务流程
- [../fronted/README.md](../fronted/README.md) — 前端页面与调用关系

---

Private & Proprietary
