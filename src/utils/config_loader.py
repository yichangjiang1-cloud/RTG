"""
工具模块：YAML 配置加载器
支持多文件合并加载，提供路径安全访问。
"""

import os
import logging
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class Config:
    """
    YAML 配置包装器，支持链式 key 访问。

    用法：
        cfg = Config.from_files("config/system.yaml", "config/vehicle_params.yaml")
        host = cfg.get("plc.host", default="127.0.0.1")
    """

    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def from_files(cls, *paths: str) -> "Config":
        """
        从一个或多个 YAML 文件加载配置，后加载的文件会覆盖先加载的同名键。

        :param paths: YAML 文件路径
        :return: Config 实例
        """
        merged: dict = {}
        for path in paths:
            if not os.path.exists(path):
                logger.warning(f"配置文件不存在，跳过: {path}")
                continue
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, data)
            logger.debug(f"已加载配置文件: {path}")
        return cls(merged)

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        通过点分隔的路径访问嵌套配置项。

        :param key_path: 如 "plc.host" 或 "gnss.receiver_a1.port"
        :param default:  键不存在时的默认值
        :return: 配置值
        """
        keys = key_path.split(".")
        node = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def require(self, key_path: str) -> Any:
        """获取必填配置项，若不存在则抛出 KeyError。"""
        value = self.get(key_path)
        if value is None:
            raise KeyError(f"必填配置项缺失: {key_path}")
        return value

    def as_dict(self) -> dict:
        """返回原始配置字典。"""
        return self._data


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并两个字典，override 中的值优先。"""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ---- 模块级全局配置单例 ----
_config: Optional[Config] = None


def init_config(*paths: str) -> Config:
    """初始化全局配置（程序启动时调用一次）。"""
    global _config
    _config = Config.from_files(*paths)
    return _config


def get_config() -> Config:
    """获取全局配置实例。"""
    if _config is None:
        raise RuntimeError("配置未初始化，请先调用 init_config()")
    return _config

