#!/bin/bash

# RTG 后端服务启动脚本 (Linux/Mac)

echo "========================================"
echo "RTG 后端服务启动脚本"
echo "========================================"
echo

# 检查 Docker 是否安装
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker 未安装"
    echo "请安装 Docker: https://docs.docker.com/get-docker/"
    exit 1
fi

echo "[INFO] 检查到 Docker 已安装"
echo

# 创建必要的目录
mkdir -p data logs
echo "[INFO] 目录已准备"
echo

# 启动服务
echo "[INFO] 正在启动服务..."
echo

docker-compose up -d

echo
echo "[INFO] 服务启动完成！"
echo
echo "后端服务地址: http://localhost:8000"
echo "API 文档: http://localhost:8000/docs"
echo "MQTT Broker: localhost:1883 (或 Docker 容器内的 mosquitto:1883)"
echo
echo "查看日志: docker-compose logs -f"
echo "停止服务: docker-compose down"
echo
