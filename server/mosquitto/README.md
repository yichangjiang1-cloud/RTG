# Mosquitto（Windows 部署）

本目录存放 RTG 项目在 **Windows 服务器**上部署 MQTT Broker（Mosquitto）的配置与安装脚本。

当前架构约定：

- **Windows**：前端、后端、MQTT Broker 同机
- **Linux 中控机**（生产）：作为 MQTT 客户端连接本机 Broker；调试期中控也可在本机

---

## 目录里有什么

| 文件 | 作用 |
|------|------|
| `mosquitto.win.conf` | **Windows 正式配置模板**（安装脚本会复制到运行目录） |
| `mosquitto.conf` | Docker/Linux 容器样例，**Windows 本机部署不会使用** |
| `install-mosquitto-service.ps1` | 一次性部署脚本：注册计划任务、部署配置、放行防火墙并启动一次 |
| `README.md` | 本说明 |

---

## 两个路径不要混

| 路径 | 角色 |
|------|------|
| `D:\tools\Mosquitto`（或你实际安装目录） | **程序本体**：`mosquitto.exe`、`mosquitto_passwd.exe` 等 |
| `D:\mosquitto` | **运行数据目录**：配置、账号密码、日志、持久化数据库 |

最终启动命令等价于：

```text
D:\tools\Mosquitto\mosquitto.exe -c D:\mosquitto\mosquitto.conf
```

`D:\mosquitto` 除了账号密码和日志外，还包括：

- `mosquitto.conf`：真正被加载的运行配置
- `data\`：持久化库（`mosquitto.db`，会话/保留消息等）
- `passwd`：用户认证文件

---

## 安装脚本做了什么

`install-mosquitto-service.ps1` **不是**日常启动工具，而是一次性部署：

1. 创建 `D:\mosquitto\data`、`D:\mosquitto\log` 并修正权限  
2. 将 `mosquitto.win.conf` 复制为 `D:\mosquitto\mosquitto.conf`  
3. 校验配置（`--test-config`）  
4. 清理旧的原生 Windows 服务 / 旧计划任务  
5. 注册计划任务 **`Mosquitto MQTT Broker`**（开机自启、崩溃重启、S4U 后台运行）  
6. 放行防火墙 TCP `1883`  
7. 立即 `Start-ScheduledTask` 并检查进程与端口  

真正拉起进程的是 **Windows 任务计划程序**，不是脚本进程本身。

为何用计划任务而不是 `mosquitto install` 服务：本机上 Mosquitto 2.1.x 原生服务模式不稳定；前台模式 `mosquitto.exe -c <conf>` 可靠，故用任务计划托管该前台模式。

---

## 使用方法

### 1. 前置条件

- 已安装 Mosquitto（默认脚本指向 `D:\tools\Mosquitto`）
- 以**管理员**打开 PowerShell

### 2. 创建账号（首次）

```powershell
mkdir D:\mosquitto -Force
& "D:\tools\Mosquitto\mosquitto_passwd.exe" -c D:\mosquitto\passwd rtg_user
```

- `-c` 会新建/覆盖密码文件，只在首次使用  
- 之后再加用户：**不要**加 `-c`

### 3. 首次部署

```powershell
cd D:\kpl\pycharmproject\RTG\server\mosquitto
.\install-mosquitto-service.ps1
```

可选参数：

```powershell
.\install-mosquitto-service.ps1 -MosquittoDir "D:\tools\Mosquitto" -RuntimeDir "D:\mosquitto"
```

成功时结尾应出现：`mosquitto is running ... listening on 1883`。

### 4. 日常启停（装好之后）

```powershell
# 启动
Start-ScheduledTask -TaskName 'Mosquitto MQTT Broker'

# 停止
Stop-ScheduledTask -TaskName 'Mosquitto MQTT Broker'

# 状态
Get-ScheduledTask -TaskName 'Mosquitto MQTT Broker' | Get-ScheduledTaskInfo

# 日志
Get-Content D:\mosquitto\log\mosquitto.log -Tail 50 -Wait
```

确认监听：

```powershell
netstat -ano | findstr :1883
```

应看到 `0.0.0.0:1883 ... LISTENING`。

### 5. 客户端连接

配置中启用了 `allow_anonymous false`，必须使用密码文件中的账号，例如：

- Host: `localhost`（本机）或服务器局域网 IP（中控机）
- Port: `1883`
- Username / Password: 创建 `passwd` 时设的账号

连错用户名密码会出现 `Not authorized` / `not authorised`。

中控机 / `config/system.yaml` 示例：

```yaml
mqtt:
  broker_host: localhost   # 生产改为 Broker 所在机 IP
  broker_port: 1883
  username: rtg_user
  password: <你的密码>
```

### 6. 修改配置后如何生效

1. 编辑仓库中的 **`mosquitto.win.conf`**（不要改 Docker 用的 `mosquitto.conf`）  
2. 再跑一次 `.\install-mosquitto-service.ps1`（会覆盖 `D:\mosquitto\mosquitto.conf` 并重建任务），或手动复制后重启计划任务  

---

## 配置要点（`mosquitto.win.conf`）

- 监听：`1883 0.0.0.0`（允许跨机连接）
- 认证：关闭匿名，`password_file D:\mosquitto\passwd`
- WebSocket：默认关闭（前端走后端 WebSocket，不直连 MQTT；避免与 GNSS UDP `9001` 端口号混淆）
- 日志：仅 error / warning / notice，避免 `log_type all` 刷盘

---

## 常见问题

| 现象 | 处理 |
|------|------|
| `Not authorized` | 检查用户名是否在 `D:\mosquitto\passwd` 中，密码是否正确 |
| 脚本报需管理员 | 用「以管理员身份运行」的 PowerShell |
| 改了仓库配置不生效 | 运行配置在 `D:\mosquitto\mosquitto.conf`，需重新部署或手动同步 |
| 进程起来又消失 | 用计划任务启停，勿依赖临时前台窗口；勿与旧原生服务混用 |

---

## 相关命令速查

```powershell
# 首次安装
.\install-mosquitto-service.ps1

# 日常
Start-ScheduledTask -TaskName 'Mosquitto MQTT Broker'
Stop-ScheduledTask  -TaskName 'Mosquitto MQTT Broker'

# 加用户（不覆盖原文件）
& "D:\tools\Mosquitto\mosquitto_passwd.exe" D:\mosquitto\passwd another_user
```
