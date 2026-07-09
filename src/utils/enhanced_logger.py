"""
工具模块：增强日志系统
自动捕获调用者信息（类名、方法名、行号），格式：时间+类名+方法名+内容
"""

import logging
import inspect
from typing import Optional
from functools import wraps


class EnhancedLogger:
    """
    增强日志记录器：自动添加类名、方法名、行号信息。

    使用方法：
        logger = EnhancedLogger.get_logger(__name__)
        logger.info("测试消息")

    日志格式示例：
        2026-05-11 10:30:45.123 [INFO] [MqttClient] [publish_telemetry] (L156) 遥测数据已发布
    """

    _loggers = {}

    @classmethod
    def get_logger(cls, name: str = None) -> 'EnhancedLogger':
        """获取日志记录器实例（单例）"""
        if name is None:
            frame = inspect.currentframe().f_back
            name = frame.f_globals.get('__name__', 'unknown')

        if name not in cls._loggers:
            cls._loggers[name] = cls(name)
        return cls._loggers[name]

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        self._name = name

    def _get_caller_info(self) -> tuple:
        """获取调用者信息（类名、方法名、行号）"""
        frame = inspect.currentframe()
        try:
            for _ in range(10):
                frame = frame.f_back
                if frame is None:
                    break

                code = frame.f_code
                filename = code.co_filename

                if 'enhanced_logger.py' in filename or 'logger.py' in filename or 'logging' in filename:
                    continue

                method_name = code.co_name
                line_no = frame.f_lineno

                class_name = None
                if 'self' in frame.f_locals:
                    class_name = frame.f_locals['self'].__class__.__name__
                elif 'cls' in frame.f_locals:
                    class_name = frame.f_locals['cls'].__name__

                return class_name, method_name, line_no
        finally:
            del frame

        return None, 'unknown', 0

    def _format_message(self, msg: str, class_name: str = None,
                       method_name: str = None, line_no: int = None) -> str:
        """格式化日志消息：[类名] [方法名] (行号) 消息"""
        if class_name is None and method_name is None:
            class_name, method_name, line_no = self._get_caller_info()

        parts = []
        if class_name:
            parts.append(f"[{class_name}]")
        if method_name:
            parts.append(f"[{method_name}]")
        if line_no:
            parts.append(f"(L{line_no})")

        prefix = " ".join(parts)
        return f"{prefix} {msg}" if prefix else msg

    def debug(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.debug(formatted)

    def info(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.info(formatted)

    def warning(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.warning(formatted)

    def error(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.error(formatted)

    def critical(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.critical(formatted)

    def exception(self, msg: str, **kwargs):
        formatted = self._format_message(msg, **kwargs)
        self._logger.exception(formatted)


def log_method_call(logger: Optional[EnhancedLogger] = None):
    """方法调用日志装饰器，自动记录方法进入和退出。"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = EnhancedLogger.get_logger(func.__module__)

            class_name = None
            if args and hasattr(args[0], '__class__'):
                class_name = args[0].__class__.__name__

            logger.debug(
                f"→ 进入 {func.__name__}",
                class_name=class_name,
                method_name=func.__name__,
            )

            try:
                result = func(*args, **kwargs)
                logger.debug(
                    f"← 退出 {func.__name__} (成功)",
                    class_name=class_name,
                    method_name=func.__name__,
                )
                return result
            except Exception as e:
                logger.error(
                    f"✗ {func.__name__} 执行异常: {e}",
                    class_name=class_name,
                    method_name=func.__name__,
                )
                raise

        return wrapper
    return decorator
