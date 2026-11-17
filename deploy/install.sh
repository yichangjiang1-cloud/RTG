#!/bin/bash
# 在 Ubuntu 上执行。程序、配置、运行数据和日志都放在 BASE 下。
# 配置只在 BASE/config/system.yaml 不存在时生成，已有配置不会被覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BASE="/home/guide/CLionProjects/GNSS"
DEST="$BASE/releases/$VERSION"
CONFIG_DIR="$BASE/config"
STATE_DIR="$BASE/state"
LOG_DIR="$BASE/logs"
RUN_USER="guide"
RUN_GROUP="guide"

if [[ ! -d "$ROOT/src" || ! -f "$ROOT/requirements.txt" ]]; then
  echo "请在解压后的发布包目录里执行 sudo bash install.sh" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 sudo bash install.sh" >&2
  exit 1
fi

if ! id "$RUN_USER" >/dev/null 2>&1; then
  echo "系统里没有用户 $RUN_USER，无法安装到 $BASE" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip

mkdir -p "$DEST" "$CONFIG_DIR" "$STATE_DIR" "$LOG_DIR"
rm -rf "$DEST/src" "$DEST/.venv"
cp -a "$ROOT/src" "$ROOT/requirements.txt" "$ROOT/VERSION" "$DEST/"

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"

ln -sfn "$DEST" "$BASE/current"

if [[ ! -f "$CONFIG_DIR/system.yaml" ]]; then
  cp "$ROOT/system.yaml.example" "$CONFIG_DIR/system.yaml"
  echo "已生成 $CONFIG_DIR/system.yaml，请把 CHANGE_ME 改成机房 Windows 的 IP，并填写 MQTT 密码。"
else
  echo "保留已有配置 $CONFIG_DIR/system.yaml"
fi

cp "$ROOT/rtg-controller.service" /etc/systemd/system/rtg-controller.service
systemctl daemon-reload
systemctl enable rtg-controller

chown -R "$RUN_USER:$RUN_GROUP" "$BASE"

echo "已安装 $VERSION -> $BASE/current"
echo "确认 $CONFIG_DIR/system.yaml 后执行: sudo systemctl restart rtg-controller"
