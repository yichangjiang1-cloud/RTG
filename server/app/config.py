from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # 应用配置
    APP_NAME: str = "RTG Backend Service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # 服务配置
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # 数据库配置（server/data 目录）
    DATABASE_URL: str = "sqlite:///./data/ground_truth.db"

    # MQTT 配置
    MQTT_BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
    MQTT_BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_QOS_TELEMETRY: int = 0  # 遥测数据不需要确认
    MQTT_QOS_CONTROL: int = 1    # 控制指令需要确认

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/backend.log"
    # 1=打印 WebSocket 下发帧摘要（telemetry 约 1Hz），便于 docker compose logs -f backend
    WS_FRAME_LOG: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
