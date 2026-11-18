"""
发布-订阅事件总线
用于各功能节点（driver / fusion / solver / bridge）之间解耦通信。

用法示例：
    bus = EventBus()
    bus.subscribe("gnss_raw", my_callback)
    bus.publish("gnss_raw", data)
"""
from __future__ import annotations

import threading
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class EventBus:
    """
    线程安全的轻量级发布-订阅总线。
    订阅者回调在发布者线程中被同步调用；
    若需异步处理，订阅者应自行将数据放入队列。
    """

    # 已定义的事件主题常量
    TOPIC_GNSS_RAW = "gnss_raw"           # driver_node → fusion_node
    TOPIC_IMU_RAW = "imu_raw"             # driver_node → fusion_node
    TOPIC_FUSED_STATE = "fused_state"     # fusion_node → solver_node
    TOPIC_POSITION_RESULT = "position_result"  # solver_node → bridge_node
    TOPIC_CALIBRATION_POINT = "calibration_point"  # 校准工具 → solver_node
    TOPIC_SYSTEM_STATUS = "system_status"  # 各节点 → 监控

    def __init__(self):
        # topic → [callback, ...] 的映射
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """
        订阅指定主题。

        :param topic:    事件主题名（推荐使用 TOPIC_* 常量）
        :param callback: 接收到事件时调用的回调函数，签名为 callback(data)
        """
        with self._lock:
            self._subscribers[topic].append(callback)
        logger.debug("订阅主题 '%s'，当前订阅者数: %d",
                     topic, len(self._subscribers[topic]))

    def unsubscribe(self, topic: str, callback: Callable) -> None:
        """取消订阅。"""
        with self._lock:
            try:
                self._subscribers[topic].remove(callback)
            except ValueError:
                logger.warning("取消订阅失败：回调不存在于主题 '%s'", topic)

    def publish(self, topic: str, data: Any) -> None:
        """
        向指定主题发布数据，同步调用所有订阅者回调。

        :param topic: 事件主题名
        :param data:  任意数据对象
        """
        with self._lock:
            callbacks = list(self._subscribers.get(topic, []))

        for cb in callbacks:
            try:
                cb(data)
            except Exception as exc:
                logger.exception("主题 '%s' 的订阅者回调异常: %s", topic, exc)


# 全局单例总线（各模块通过 import 共享同一实例）
_global_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    """获取全局单例 EventBus。"""
    global _global_bus
    if _global_bus is None:
        with _bus_lock:
            if _global_bus is None:
                _global_bus = EventBus()
    return _global_bus

