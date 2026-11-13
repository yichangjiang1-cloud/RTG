import logging
import os
from logging.handlers import RotatingFileHandler
from app.config import settings

def setup_logging():
    """初始化日志系统"""
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    # 创建日志目录
    log_dir = os.path.dirname(settings.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 创建日志格式器
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # 文件处理器（滚动）
    file_handler = RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    # 获取根日志记录器
    logger = logging.getLogger()
    logger.setLevel(log_level)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger

def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志记录器"""
    return logging.getLogger(name)


def get_frontend_file_logger() -> logging.Logger:
    """浏览器上报的日志，单独写入 logs/frontend.log，不进 backend.log。"""
    log = logging.getLogger("rtg.frontend")
    if log.handlers:
        return log
    log_dir = os.path.dirname(settings.LOG_FILE) or "logs"
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "frontend.log")
    handler = RotatingFileHandler(
        path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.propagate = False
    log.info("前端日志文件已打开: %s", path)
    return log
