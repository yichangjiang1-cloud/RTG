"""
工具模块：日志配置
- 控制台 + 会话 system.log（连接/告警等）
- position.log 仅落盘，不刷终端（坐标/姿态排查用）
每次启动新建 logs/session_YYYYMMDD_HHMMSS/ 目录。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from datetime import datetime
from typing import Optional, Tuple

from .enhanced_logger import EnhancedLogger, log_method_call

__all__ = [
    "setup_logging",
    "get_session_log_dir",
    "get_position_logger",
    "EnhancedLogger",
    "log_method_call",
]

_SESSION_DIR: Optional[str] = None
_POSITION_LOGGER_NAME = "rtg.position"


def get_session_log_dir() -> Optional[str]:
    """当前进程会话日志目录（setup_logging 之后才有值）。"""
    return _SESSION_DIR


def get_position_logger() -> logging.Logger:
    """仅写 position.log 的 logger（不向控制台传播）。"""
    return logging.getLogger(_POSITION_LOGGER_NAME)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    session_root: Optional[str] = None,
) -> str:
    """
    初始化全局日志。应在程序启动时调用一次。

    :param level:         日志级别
    :param log_file:      兼容旧配置；若给出且含目录，其父目录作为 logs 根，
                          实际写入 session_*/system.log
    :param max_bytes:     单文件滚动大小
    :param backup_count:  滚动备份数
    :param session_root:  会话根目录，默认取 log_file 父目录或 logs/
    :return:              本次会话目录绝对/相对路径
    """
    global _SESSION_DIR

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    if session_root:
        root = session_root
    elif log_file:
        root = os.path.dirname(log_file) or "logs"
    else:
        root = "logs"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = os.path.join(root, f"session_{stamp}")
    os.makedirs(session_dir, exist_ok=True)
    _SESSION_DIR = session_dir

    system_path = os.path.join(session_dir, "system.log")
    position_path = os.path.join(session_dir, "position.log")

    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)-25s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    system_handler = logging.handlers.RotatingFileHandler(
        system_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    system_handler.setFormatter(formatter)
    root_logger.addHandler(system_handler)

    # 兼容：在 logs/ 下写一份最新会话指针，便于打开
    try:
        latest = os.path.join(root, "LATEST_SESSION.txt")
        with open(latest, "w", encoding="utf-8") as f:
            f.write(session_dir.replace("\\", "/") + "\n")
    except OSError:
        pass

    # 旧路径 logs/rtg.log → 软提示用：再挂一个按日滚动的汇总（可选，便于习惯旧路径）
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
            legacy = logging.handlers.TimedRotatingFileHandler(
                log_file,
                when="midnight",
                interval=1,
                backupCount=max(backup_count, 7),
                encoding="utf-8",
            )
            legacy.suffix = "%Y%m%d"
            legacy.setFormatter(formatter)
            root_logger.addHandler(legacy)
        except OSError:
            pass

    pos_logger = logging.getLogger(_POSITION_LOGGER_NAME)
    pos_logger.setLevel(logging.INFO)
    pos_logger.handlers.clear()
    pos_logger.propagate = False
    pos_fh = logging.handlers.RotatingFileHandler(
        position_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    pos_fh.setFormatter(formatter)
    pos_logger.addHandler(pos_fh)

    logging.getLogger("rtg").info(
        "✓ 日志系统初始化完成，级别: %s | 会话目录: %s", level, session_dir
    )
    pos_logger.info("position 日志开始（仅文件，不输出终端） session=%s", session_dir)
    return session_dir
