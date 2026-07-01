"""
通信节点：bridge_node
订阅 solver_node 输出的定位结果，编码为 PLC 协议帧并通过 TCP 下发。
按配置的帧率（≥10Hz）周期性发送。
"""

import logging
import time
import threading
from typing import Optional

from src.core.event_bus import get_bus, EventBus
from src.models.positioning import PositionResult
from src.bridge.protocol import encode_frame
from src.bridge.plc_client import PlcClient
from src.utils.config_loader import get_config

logger = logging.getLogger(__name__)


class BridgeNode:
    """
    通信节点：负责将定位结果下发给 PLC。

    数据流：
      solver_node → TOPIC_POSITION_RESULT → BridgeNode → PLC (TCP)

    工作模式：
      - 订阅定位结果
      - 按配置帧率周期性打包最新数据并发送
      - 维护 PLC 连接状态
    """

    def __init__(self, bus: Optional[EventBus] = None):
        cfg = get_config()
        self._bus = bus or get_bus()

        # PLC 连接参数
        self._plc_enabled = bool(cfg.get("plc.enabled", True))
        plc_host = cfg.get("plc.host", "192.168.1.200")
        plc_port = cfg.get("plc.port", 5000)
        reconnect_interval = cfg.get("plc.reconnect_interval_s", 3.0)

        # PLC 客户端（联调可关闭）
        self._plc_client: Optional[PlcClient] = None
        if self._plc_enabled:
            self._plc_client = PlcClient(
                host=plc_host,
                port=plc_port,
                reconnect_interval=reconnect_interval,
            )
        else:
            logger.info("PLC 已禁用（plc.enabled=false），跳过 TCP 连接")

        # 配置的目标帧率（Hz）
        self._target_rate_hz = cfg.get("plc.frame_rate_hz", 10)
        self._send_interval  = 1.0 / self._target_rate_hz

        # 最新定位结果缓存（供周期发送线程读取）
        self._latest_result: Optional[PositionResult] = None
        self._result_lock = threading.Lock()

        # 帧序列号
        self._seq = 0

        # 周期发送线程
        self._send_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 订阅定位结果
        self._bus.subscribe(EventBus.TOPIC_POSITION_RESULT, self._on_position_result)

        logger.info(f"BridgeNode 初始化完成，目标帧率 {self._target_rate_hz} Hz")

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动 bridge_node（PLC 客户端 + 周期发送线程）。"""
        if self._plc_client:
            self._plc_client.start()
        self._send_thread = threading.Thread(
            target=self._send_loop,
            name="BridgeSendLoop",
            daemon=True,
        )
        self._send_thread.start()
        logger.info("BridgeNode 已启动")

    def stop(self) -> None:
        """停止 bridge_node。"""
        logger.info("BridgeNode 停止中...")
        self._stop_event.set()
        if self._send_thread:
            self._send_thread.join(timeout=3.0)
        if self._plc_client:
            self._plc_client.stop()
            self._plc_client.join(timeout=3.0)
        logger.info("BridgeNode 已停止")

    # ------------------------------------------------------------------
    # 回调：接收定位结果
    # ------------------------------------------------------------------
    def _on_position_result(self, result: PositionResult) -> None:
        """
        收到 solver_node 发布的定位结果，更新到缓存。
        实际发送由周期线程控制（保证稳定帧率）。
        """
        with self._result_lock:
            self._latest_result = result

    # ------------------------------------------------------------------
    # 周期发送线程
    # ------------------------------------------------------------------
    def _send_loop(self) -> None:
        """
        周期性打包并发送最新定位结果到 PLC。
        即使没有新数据也按帧率持续发送（保持心跳）。
        """
        logger.info("BridgeNode 周期发送线程启动，间隔 %.3f s", self._send_interval)
        next_send_time = time.time()

        while not self._stop_event.is_set():
            now = time.time()
            if now < next_send_time:
                time.sleep(max(0.001, next_send_time - now))
                continue

            # 读取最新结果
            with self._result_lock:
                result = self._latest_result

            if result is None or not self._plc_client:
                # 尚未收到任何定位结果，或 PLC 已禁用
                next_send_time += self._send_interval
                continue

            # 编码并发送
            try:
                frame = encode_frame(result, self._seq)
                self._plc_client.send(frame)
                
                self._seq += 1
            except Exception as exc:
                logger.exception(f"编码/发送帧失败: {exc}")

            # 计算下次发送时刻（严格按周期，避免累积漂移）
            next_send_time += self._send_interval

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def is_plc_connected(self) -> bool:
        """PLC 是否已连接。"""
        if not self._plc_client:
            return False
        return self._plc_client.is_connected

    def get_stats(self) -> dict:
        """返回发送统计信息。"""
        if not self._plc_client:
            return {"sent": 0, "dropped": 0, "errors": 0, "target_rate_hz": self._target_rate_hz}
        stats = self._plc_client.stats
        stats["target_rate_hz"] = self._target_rate_hz
        return stats

