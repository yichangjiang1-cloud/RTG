# 中控打包与部署指南（大车 Ubuntu）

面向第一次把中控装上车的同事。中控是大车上的解算程序，**不进 Docker**。  
机房 Windows 上的前端、后端、MQTT 仍按 [server/DEPLOY_DOCKER.md](../server/DEPLOY_DOCKER.md) 用 Compose 跑。

本机 WSL 只用来在不到现场时验证安装包能否装上、能否连上本机 Docker。WSL 里填的 IP **不能**原样抄到大车上。

---

## 0. 先搞清谁在哪

| 程序 | 跑在哪 | 怎么启动 |
|------|--------|----------|
| Mosquitto、后端、前端 | 机房 Windows 的 Docker | `docker compose up -d` |
| 中控 | 每台大车一台 Ubuntu | 发布包 + `sudo bash install.sh`，由 systemd 拉起 |

中控做三件事：收本车 GNSS、解算、把结果发到机房的 MQTT `1883`，启动时再访问后端 `8000` 拉参数和堆场校准。浏览器只访问 Windows 上的前端 `80`。

GNSS 不进 Docker。接收机把数据送到**中控机的 IP**，不是送到 Windows。

### 程序和配置分开

| 内容 | 路径 | 升级时 |
|------|------|--------|
| 程序与虚拟环境 | `/opt/rtg/releases/<版本>`，`/opt/rtg/current` 指向正在用的版本 | 换 `current` |
| 人改的配置 | `/etc/rtg/system.yaml` | **不覆盖** |
| 程序自己写的备份 | `/var/lib/rtg/`（`vehicle_params.yaml`、`yard_config.yaml`） | 保留 |
| 运行日志 | `/var/log/rtg/session_日期时间/` | 每次启动新建一个会话目录 |
| GNSS 原始报文 | `/var/lib/rtg/gnss_raw/` | 按接收机分子目录 |

开发机不设环境变量时，仍读仓库里的 `config/`，和车上这套路径互不影响。

---

## 1. 在 Windows 上打包

在仓库根目录：

```powershell
cd D:\kpl\pycharmproject\RTG
powershell -File deploy\package.ps1 -Version 1.0.0
```

得到 `dist\rtg-controller-1.0.0.tar.gz`。包里有 `src/`、`requirements.txt`、`install.sh`、`rtg-controller.service`、`system.yaml.example`。  
不含 `server/`、`fronted/`，也不含开发机上的 `config/system.yaml`（里面是本机地址）。  
虚拟环境不打进包，必须到 Ubuntu 上现装，Windows 的 `.venv` 不能拷过去。

---

## 2. 大车 Ubuntu（正式部署）

每台车做一次。下面假设发布包已拷到车上，例如 `/tmp`。

### 2.1 安装

```bash
tar -xzf rtg-controller-1.0.0.tar.gz
cd rtg-controller-1.0.0
sudo bash install.sh    会执行systemctl enable rtg-controller 开机自启    systemctl is-enabled rtg-controller
```

脚本会：

1. 安装 `python3`、`python3-venv`，在 `/opt/rtg/releases/1.0.0` 创建虚拟环境并 `pip install`
2. 把 `/opt/rtg/current` 指到这一版
3. **仅当** `/etc/rtg/system.yaml` 不存在时，从模板生成它
4. 注册并 enable `rtg-controller.service`（开机自启，异常退出 3 秒后拉起） 

第一次安装后先改配置再启动。车上要能访问外网或内网 pip，用来装 numpy 等依赖。

`system.yaml` 在 Ubuntu 系统目录里，不在解压出来的发布包目录里。发布包里只有模板 `system.yaml.example`。

### 2.2 填写 `/etc/rtg/system.yaml`

```bash
sudo nano /etc/rtg/system.yaml
```

把所有 `CHANGE_ME` 换成**机房那台 Windows 的局域网 IP**（`ipconfig` 里插上网线的那块网卡，例如「以太网」的 `172.16.20.149`）。不要填 `localhost`，不要填 WSL 的 `172.23.x.x`。

MQTT 的用户名、密码与机房 `server/mosquitto/passwd`、`server/.env` 里的 `MQTT_USERNAME` / `MQTT_PASSWORD` 相同。

一台车一个 `vehicle_id`。新车可先留空或保持临时 ID，等前端初始化后再写入。

GNSS 按这台车的实际接线改：

| 接收机 | 配置 | 含义 |
|--------|------|------|
| `receiver_a1`（左，`gantry_a1`） | `transport: tcp`，`host` 为接收机 IP | 中控主动去连接收机 |
| `receiver_a2`（右，`gantry_a2`） | `transport: udp`，`host: 0.0.0.0`，`port: 9002` | 中控在本机听；接收机的目标 IP 填这台 Ubuntu，端口 `9002` |
| `receiver_b`（小车） | `transport: udp`，`host: 0.0.0.0`，`port: 9003` | 同上，端口 `9003` |

`plc.enabled` 默认 `false`。要下发定位时，把 `plc.host` 改成车上 PLC 地址再打开。

### 2.3 启动与验收

```bash
sudo systemctl restart rtg-controller
systemctl status rtg-controller
journalctl -u rtg-controller -n 50 --no-pager
```

日志里应同时有：

- `配置文件: /etc/rtg/system.yaml`
- 后端地址是机房 Windows 的 `http://<局域网IP>:8000`，且连接正常
- MQTT 已连上同一 IP 的 `1883`
- 当前 `vehicle_id` 与这台车一致

然后在机房浏览器打开前端，设备列表里能看到这台中控。没有 GNSS 时定位为空，只说明通信通了。

机房 Windows 防火墙对大车网段放行 **8000** 和 **1883**。Ubuntu 若开了防火墙，放行接收机打进来的 UDP `9002`、`9003`。

### 2.4 升级与回退

开发机重新打包，例如 `-Version 1.0.1`。车上解压后再执行一次 `sudo bash install.sh`。  
已有的 `/etc/rtg/system.yaml` 会保留，终端会打印「保留已有配置」。`current` 改指向新版本。

退回上一版：

```bash
sudo ln -sfn /opt/rtg/releases/1.0.0 /opt/rtg/current
sudo systemctl restart rtg-controller
```

不要在车上改 `/opt/rtg/current` 里的源码。要改程序就在开发机改完，打下一个版本。

---

## 3. 本机 WSL（不到现场时的验证）

WSL 里的 Ubuntu 和 Windows 不是同一张网。中控跑在 WSL 里，Docker 跑在 Windows 上。

```text
中控（WSL）  172.23.134.143
    │  虚拟网线
    ▼
Windows 虚拟网卡  172.23.128.1     ← WSL 访问本机 Docker 用这个
Windows 实体网卡  172.16.20.149    ← 大车从办公室网络访问 Windows 用这个
```

`/etc/resolv.conf` 里的 `10.255.255.254` 是 WSL 自己的 DNS，**不能**填进 `system.yaml`。

### 3.1 前置

1. WSL 已启用 systemd。`/etc/wsl.conf` 中有：

```ini
[boot]
systemd=true
```

改过这项后需要在 Windows 执行一次 `wsl --shutdown`，再重新打开 Ubuntu。  
2. Windows 上打开 Docker Desktop，在 `server` 目录执行 `docker compose up -d`，确认 `8000`、`1883` 在听。

### 3.2 安装

发布包在 Windows 盘上时，WSL 路径是 `/mnt/d/...`：

```bash
cd /mnt/d/kpl/pycharmproject/RTG/dist
tar -xzf rtg-controller-1.0.0.tar.gz
cd rtg-controller-1.0.0
sudo bash install.sh
```

安装结束只会提示「已生成 `/etc/rtg/system.yaml`」。这个文件在 WSL 的 Linux 文件系统里，**不会**出现在解压目录或 Windows 工程目录。

查看：

```bash
ls -l /etc/rtg/system.yaml
```

从 Windows 资源管理器打开：`\\wsl$\Ubuntu\etc\rtg`

### 3.3 填写地址

先确认 WSL 看 Windows 的地址（默认路由的下一跳）：

```bash
ip route show default
```

本机验证时这一跳是 `172.23.128.1`。把 `/etc/rtg/system.yaml` 里的 `CHANGE_ME` 都换成它，MQTT 密码与 `.env` 相同。

先测端口，再启动中控：

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://172.23.128.1:8000/api/health', timeout=5).read().decode())"
```

能打印 `healthy` 再执行：

```bash
sudo systemctl restart rtg-controller
journalctl -u rtg-controller -n 50 --no-pager
```

Docker 没启动时，这条地址会超时，不代表 IP 填错。引擎起来并 `docker compose up -d` 之后再测。若 Docker 已在听、WSL 仍然超时，在 Windows 防火墙里对虚拟网卡 `vEthernet (WSL)` 放行 `8000` 和 `1883`。

### 3.4 模拟器往哪发

Windows 上的大车模拟器（`CarMode` 的 `SimConfig.targetHost`）不要填 `127.0.0.1`，UDP 到不了 WSL。填 WSL 自己的地址：

```bash
hostname -I
```

本机曾是 `172.23.134.143`。WSL 重启后可能变，以当时 `hostname -I` 为准。

中控当前在听的端口：

| 模拟器字段 | 端口 | 中控 |
|------------|------|------|
| `leftReceiverPort` | 9002 | `gantry_a2`，UDP 在听 |
| `trolleyPort` | 9003 | 小车，UDP 在听 |
| `rightReceiverPort` | 9001 | 当前 **没有** 在听。`gantry_a1` 是中控主动 TCP 去连接收机 |

WSL 重启后 `172.23.128.1` 也可能变，以 `ip route show default` 为准。这组地址只用于本机验证，上车后改回第 2 节的局域网 IP 和真实接收机地址。

---

## 4. 日志在哪

实时运行日志：

```bash
journalctl -u rtg-controller -f
```

落盘日志在 `/var/log/rtg/`。每次启动新建 `session_日期时间/`，当前会话路径写在 `/var/log/rtg/LATEST_SESSION.txt`。

| 文件 | 内容 |
|------|------|
| `session_.../system.log` | 连接、告警、解算过程 |
| `session_.../position.log` | 坐标、姿态 |

GNSS 原文在 `/var/lib/rtg/gnss_raw/`：

| 目录 | 接收机 |
|------|--------|
| `receiver_1_gantry_a1/` | 左 |
| `receiver_2_gantry_a2/` | 右 |
| `receiver_3_trolley/` | 小车 |

断流超过 `gnss.raw_log.session_gap_s`（默认 2 秒）会新开一个 `.nmea` 文件。

```bash
tail -f /var/log/rtg/session_*/system.log
tail -f /var/lib/rtg/gnss_raw/receiver_2_gantry_a2/*.nmea
```

Windows 资源管理器：`\\wsl$\Ubuntu\var\log\rtg` 与 `\\wsl$\Ubuntu\var\lib\rtg\gnss_raw`。大车上没有 `\\wsl$`，直接在 Ubuntu 里看上述路径。

---

## 5. 常用命令

```bash
sudo systemctl restart rtg-controller     重启中控程序
sudo systemctl stop rtg-controller        结束中控程序
systemctl is-active rtg-controller        查看程序是否正在运行
sudo systemctl disable rtg-controller     设置成不要开机自启
systemctl status rtg-controller
journalctl -u rtg-controller -n 80 --no-pager
ls -l /opt/rtg/current
journalctl -u rtg-controller.service -f -l 日志查看
```

---

## 6. 对照

| 事项 | 大车 Ubuntu | 本机 WSL |
|------|-------------|----------|
| 中控访问 Windows 的 IP | 以太网局域网地址，如 `172.16.20.149` | `ip route` 的下一跳，如 `172.23.128.1` |
| 模拟器 `targetHost` | 不使用；接收机发给中控机 IP | `hostname -I` 的 WSL 地址 |
| `system.yaml` 位置 | `/etc/rtg/system.yaml` | 同一个路径，但是 WSL 里的文件 |
| 升级是否覆盖配置 | 否 | 否 |
