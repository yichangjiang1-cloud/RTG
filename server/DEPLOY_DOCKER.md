# Docker 打包与部署指南（后端 + MQTT）

面向第一次使用 Docker 的同事。本文只讲 **Windows 上用 Docker Compose 跑「Mosquitto + 后端」**。

前端、中控不在此 Compose 内。  
本机 `D:\tools\Mosquitto` / `D:\mosquitto` 是**裸机计划任务**方案，与 Docker **无关、不必打包**。

---

## 0. 先搞清几个词

| 词 | 含义 | 你项目里的例子 |
|----|------|----------------|
| **镜像 Image** | 只读模板（装好依赖的“安装包”） | `server-backend:latest`、`eclipse-mosquitto:2` |
| **容器 Container** | 镜像跑起来的进程实例 | `rtg-backend`、`rtg-mosquitto` |
| **Compose** | 一份 yaml 同时管多个容器 | `docker-compose.yml` |
| **打包** | 把镜像导出成 `.tar` + 带上配置文件 | 见下文「打包」 |
| **部署** | 在目标机器 `load` 镜像后 `compose up` | 见下文「部署」 |

本机日常调试：

```powershell
cd D:\kpl\pycharmproject\RTG\server
# Docker Desktop 必须已打开（Engine running）
docker compose up -d --build
```
docker compose down

这会起三个容器：

| 容器 | 镜像 | 端口 |
|------|------|------|
| `rtg-mosquitto` | `eclipse-mosquitto:2` | 1883 |
| `rtg-backend` | `server-backend:latest` | 8000 |
| `rtg-frontend` | `rtg-frontend:latest` | **80**（浏览器打开 http://localhost ） |

前端镜像在构建时会把 `fronted/` 源码 `npm run build` 打进 Nginx；**不需要**在生产机装 Node。

浏览器打开前端后，在「设置 → 后端服务」里：

- **Docker 同源部署**：Host 填 `auto`（走 Nginx 反代 `/api`、`/ws`）
- **本机 Vite 开发**：Host=`localhost`，Port=`8000`

---

## 1. 账号密码分别是什么（部署必懂）

| 文件 | 谁用 | 作用 |
|------|------|------|
| `mosquitto/passwd` | **Mosquitto 容器** | Broker **验密**（门锁钥匙本） |
| `.env` 里的 `MQTT_USERNAME` / `MQTT_PASSWORD` | **后端容器** | 后端连 Broker 时带的账号（口袋里的钥匙） |

两者必须一致。MQTT Explorer / 中控也要和 `passwd` 一致。

生成/重置 passwd（在 `server` 目录，且 **passwd 必须是文件不是目录**）：

```powershell
cd D:\kpl\pycharmproject\RTG\server
# 若误建成目录，先删掉再生成
& "D:\tools\Mosquitto\mosquitto_passwd.exe" -c .\mosquitto\passwd rtg_user
```

然后把同样的密码写进 `.env`。

---

## 2. 本机打包（开发机操作）

目标：得到可拷贝到 U 盘 / 另一台电脑的发布目录。

### 2.1 前置

1. 打开 **Docker Desktop**，等引擎就绪  
2. 确认能执行：`docker version`（Client、Server 都有）  
3. 停本机计划任务 Broker，避免和打包验证抢端口：

```powershell
Stop-ScheduledTask -TaskName 'Mosquitto MQTT Broker' -ErrorAction SilentlyContinue
```

### 2.2 构建镜像

```powershell
cd D:\kpl\pycharmproject\RTG\server
docker compose build
docker images
```

你应能看到类似：

```text
eclipse-mosquitto:2
server-backend:latest
```

（有 `latest` 标签的 mosquitto 也可以，Compose 用的是 `:2`。）

### 2.3 导出镜像为 tar

```powershell
mkdir D:\rtg-release -Force

docker save -o D:\rtg-release\server-backend.tar server-backend:latest
docker save -o D:\rtg-release\eclipse-mosquitto-2.tar eclipse-mosquitto:2
docker save -o D:\rtg-release\rtg-frontend.tar rtg-frontend:latest
```

`docker save` = 把镜像打成文件，方便离线拷贝。  
生产机**有网**时，Mosquitto 也可以不导出，到那边 `docker pull eclipse-mosquitto:2`。  
前端/后端业务镜像一般需要 `save` 带走（或推到私有镜像仓库）。

### 2.4 复制配置与 Compose（不进镜像的部分）

```powershell
$dst = "D:\rtg-release"
New-Item "$dst\mosquitto","$dst\data","$dst\logs" -ItemType Directory -Force | Out-Null

Copy-Item .\docker-compose.yml $dst\
Copy-Item .\.env.example $dst\.env.example
Copy-Item .\.env $dst\.env                    # 生产请改成生产密码
Copy-Item .\mosquitto\mosquitto.conf $dst\mosquitto\
Copy-Item .\mosquitto\passwd $dst\mosquitto\  # 必须是文件
```

最终目录建议：

```text
D:\rtg-release\
├── server-backend.tar
├── rtg-frontend.tar
├── eclipse-mosquitto-2.tar      # 可选（有网可 pull）
├── docker-compose.yml
├── .env                         # 生产密码
├── .env.example
├── mosquitto\
│   ├── mosquitto.conf
│   └── passwd                   # 文件！
├── data\                        # 空目录即可，运行后出 SQLite
└── logs\
```

**不要**打包：`D:\tools\Mosquitto`、`D:\mosquitto`、整个 Git 源码树（除非你还要在生产机上重新 `build`）。
前端源码也不必带到生产机——已经打进 `rtg-frontend.tar`。

### 2.5（可选）本机先验证发布包能否起

```powershell
cd D:\rtg-release
docker compose down
docker compose up -d
docker compose ps
```

能看到两个 `Up` / `healthy`，再 `docker compose down`，把整个 `D:\rtg-release` 拷走即可。

---

## 3. 生产机部署（目标机器操作）

### 3.1 生产机只需要

1. 安装并打开 **Docker Desktop**（或 Linux 上的 Docker Engine + Compose 插件）  
2. 拷贝整个 `rtg-release` 目录  
3. **不需要**安装 `D:\tools\Mosquitto`

### 3.2 加载镜像

```powershell
cd D:\rtg-release   # 改成你的实际路径

docker load -i .\server-backend.tar
docker load -i .\rtg-frontend.tar
docker load -i .\eclipse-mosquitto-2.tar   # 若带了该文件

docker images
```

应能看到 `server-backend:latest`、`rtg-frontend:latest` 和 `eclipse-mosquitto:2`。

若没带 mosquitto tar、生产机有网：

```powershell
docker pull eclipse-mosquitto:2
```

### 3.3 检查配置

1. 确认 `mosquitto\passwd` 是**文件**不是文件夹  
2. 编辑 `.env`，密码与 `passwd`、中控 `system.yaml` 一致  
3. 防火墙如需外网访问，放行 **8000**（API）和 **1883**（MQTT）

### 3.4 启动

```powershell
cd D:\rtg-release
docker compose up -d
docker compose ps
```

期望：

| NAME | 端口 |
|------|------|
| `rtg-frontend` | `0.0.0.0:80->80` |
| `rtg-backend` | `0.0.0.0:8000->8000` |
| `rtg-mosquitto` | `0.0.0.0:1883->1883` |

验证：

- 浏览器：http://localhost （前端）；设置里 Host 填 **`auto`**
- API 文档：http://localhost:8000/docs  
- MQTT Explorer：`localhost:1883` + `.env` 里同一套账号  

若本机 80 端口被占用，把 `docker-compose.yml` 里 frontend 的 `"80:80"` 改成 `"8080:80"`，然后访问 http://localhost:8080 。  

### 3.5 生产机日常命令

```powershell
cd D:\rtg-release

docker compose up -d          # 启动
docker compose stop           # 停止（保留容器）
docker compose start          # 再启动
docker compose down           # 停止并删除容器（data/ 里库文件一般还在）
docker compose logs -f        # 看日志
docker compose logs -f backend
docker compose ps             # 状态
```

不用时：`docker compose stop`。若还要关 Docker Desktop 本身，托盘图标 Quit（省内存）。

---

## 4. 改代码后如何重新打包

1. 在开发机改 `server/app/` 或 `fronted/` 代码  
2. `docker compose build`（或 `up -d --build`）  
3. 再 `docker save` 对应镜像：  
   - 只改后端 → `server-backend.tar`  
   - 只改前端 → `rtg-frontend.tar`  
4. 生产机 `docker load` 后 `docker compose up -d`

只改 `.env` / `passwd` / `mosquitto.conf`：不用重新 `save` 镜像，改文件后：

```powershell
docker compose up -d          # 或 restart 对应服务
docker compose restart mosquitto
```

---

## 5. 常见问题（第一次必看）

| 现象 | 原因 | 处理 |
|------|------|------|
| `failed to connect ... dockerDesktopLinuxEngine` | Desktop 没开 | 打开 Docker Desktop 等到 Engine running |
| `unable to open pwfile ... passwd` | passwd 是目录或缺失 | 删目录，用 `mosquitto_passwd -c` 生成文件 |
| `port is already allocated` 1883 | 本机计划任务 Broker 还在 | `Stop-ScheduledTask 'Mosquitto MQTT Broker'` |
| MQTT `Not authorized` | 密码与 passwd 不一致 | 统一 `.env` / Explorer / 中控 / passwd |
| 生产机 `compose up` 又去 build 失败 | 无源码/无网 | 确认已 `docker load`，且 compose 中有 `image: ...:latest` |
| `port is already allocated` 80 | 本机 IIS/其它占 80 | 改 frontend 为 `"8080:80"` |
| 数据库丢了 | `down -v` 删了卷，或删了 `data/` | 备份 `data/ground_truth.db`；不要轻易 `-v` |

---

## 6. 和「裸机 Mosquitto」怎么选

| 模式 | 何时用 |
|------|--------|
| **Docker Compose（本文）** | 一键起 Broker + 后端 + 前端；发布靠镜像 tar |
| **计划任务 + D:\mosquitto** | 本机调试 Broker、或不想开 Docker Desktop |

**同一台机器同一时间只跑一种 Broker**，都占 `1883`。

---

## 7. 命令速查卡

```powershell
# —— 开发机打包 ——
cd D:\kpl\pycharmproject\RTG\server
docker compose build
docker save -o D:\rtg-release\server-backend.tar server-backend:latest
docker save -o D:\rtg-release\rtg-frontend.tar rtg-frontend:latest
docker save -o D:\rtg-release\eclipse-mosquitto-2.tar eclipse-mosquitto:2
# 再复制 compose / .env / mosquitto/* / data / logs

# —— 生产机部署 ——
cd D:\rtg-release
docker load -i .\server-backend.tar
docker load -i .\rtg-frontend.tar
docker load -i .\eclipse-mosquitto-2.tar
docker compose up -d
docker compose ps
# 浏览器 http://localhost ，设置 Host=auto

# —— 启停 ——
docker compose stop
docker compose up -d
```