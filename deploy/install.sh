#!/bin/bash
# 在大车 Ubuntu 上执行。把当前目录这版程序装到 /opt/rtg/releases/<版本>，
# 配置只在 /etc/rtg/system.yaml 不存在时生成，已有配置不会被覆盖。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DEST="/opt/rtg/releases/$VERSION"

if [[ ! -d "$ROOT/src" || ! -f "$ROOT/requirements.txt" ]]; then
  echo "请在解压后的发布包目录里执行 sudo bash install.sh" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请用 sudo bash install.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip

mkdir -p "$DEST" /etc/rtg /var/lib/rtg /var/log/rtg
rm -rf "$DEST/src" "$DEST/.venv"
cp -a "$ROOT/src" "$ROOT/requirements.txt" "$ROOT/VERSION" "$DEST/"

python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --upgrade pip
"$DEST/.venv/bin/pip" install -r "$DEST/requirements.txt"

ln -sfn "$DEST" /opt/rtg/current

if [[ ! -f /etc/rtg/system.yaml ]]; then
  cp "$ROOT/system.yaml.example" /etc/rtg/system.yaml
  echo "已生成 /etc/rtg/system.yaml，请把 CHANGE_ME 改成机房 Windows 的 IP，并填写 MQTT 密码。"
else
  echo "保留已有配置 /etc/rtg/system.yaml"
fi

cp "$ROOT/rtg-controller.service" /etc/systemd/system/rtg-controller.service
systemctl daemon-reload
systemctl enable rtg-controller

echo "已安装 $VERSION -> /opt/rtg/current"
echo "确认 /etc/rtg/system.yaml 后执行: sudo systemctl restart rtg-controller"
