@echo off
REM RTG 后端服务启动脚本 (Windows)

echo ========================================
echo RTG 后端服务启动脚本
echo ========================================
echo.

REM 检查 Docker 是否安装
where docker > nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker 未安装或不在系统路径中
    echo 请安装 Docker Desktop: https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

echo [INFO] 检查到 Docker 已安装
echo.

REM 检查 docker-compose
where docker-compose > nul 2>&1
if errorlevel 1 (
    echo [WARN] docker-compose 未找到，尝试使用 docker compose
    set COMPOSE_CMD=docker compose
) else (
    set COMPOSE_CMD=docker-compose
)

echo [INFO] 使用命令: %COMPOSE_CMD%
echo.

REM 创建必要的目录
if not exist "data" mkdir data
if not exist "logs" mkdir logs
echo [INFO] 目录已准备

echo.
echo [INFO] 正在启动服务...
echo.

%COMPOSE_CMD% up -d

echo.
echo [INFO] 服务启动完成！
echo.
echo 后端服务地址: http://localhost:8000
echo API 文档: http://localhost:8000/docs
echo MQTT Broker: localhost:1883 (或 Docker 容器内的 mosquitto:1883)
echo.
echo 查看日志: %COMPOSE_CMD% logs -f
echo 停止服务: %COMPOSE_CMD% down
echo.

pause
