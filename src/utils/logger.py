"""
工具模块：日志配置
统一初始化 logging，支持控制台 + 滚动文件双输出。
增强功能：导出 EnhancedLogger 用于自动捕获调用者信息。
"""

import logging
import logging.handlers
import os
from typing import Optional

# 导出增强日志类
from .enhanced_logger import EnhancedLogger, log_method_call

__all__ = ['setup_logging', 'EnhancedLogger', 'log_method_call']


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> None:
    """
    初始化全局日志配置。应在程序启动时调用一次。

    :param level:        日志级别字符串，如 "DEBUG"、"INFO"
    :param log_file:     日志文件路径；为 None 时仅输出到控制台
    :param max_bytes:    单个日志文件最大字节数（默认 10MB）
    :param backup_count: 保留的历史日志文件数量
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # 增强日志格式：添加毫秒
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)-25s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # 清除已有 Handler（避免重复初始化）
    root_logger.handlers.clear()

    # 控制台 Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 文件 Handler（滚动）
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("rtg").info("✓ 日志系统初始化完成，级别: %s", level)


