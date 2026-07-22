"""

通信节点：bridge_node

订阅 solver_node 输出的定位结果，编码为 PLC V1.0.0 协议帧并通过 TCP 下发。

按配置的帧率（≥10Hz）周期性发送，并接收 PLC 下行心跳/指令。

"""



import logging

import time

import threading

from typing import Optional



from src.core.event_bus import get_bus, EventBus

from src.models.positioning import PositionResult

from src.bridge.plc_protocol_v1 import (

    PlcProtocolContext,

    PlcToGnssFrame,

    encode_gnss_to_plc,

)

from src.bridge.plc_client import PlcClient

from src.utils.config_loader import get_config



logger = logging.getLogger(__name__)





class BridgeNode:

    """

    通信节点：负责将定位结果下发给 PLC（协议 V1.0.0）。



    数据流：

      solver_node → TOPIC_POSITION_RESULT → BridgeNode → PLC (TCP)

      PLC → BridgeNode（心跳/位置反馈，更新 peer_heartbeat）

    """



    def __init__(self, bus: Optional[EventBus] = None):

        cfg = get_config()

        self._bus = bus or get_bus()



        self._plc_enabled = bool(cfg.get("plc.enabled", True))

        plc_host = cfg.get("plc.host", "192.168.1.200")

        plc_port = cfg.get("plc.port", 5000)

        reconnect_interval = cfg.get("plc.reconnect_interval_s", 3.0)



        self._plc_ctx = PlcProtocolContext(
            vehicle_id=str(cfg.get("vehicle_id", cfg.get("plc.vehicle_id", "RTG-001"))),
            yard_in_out=int(cfg.get("plc.yard_in_out", 1)),
            cross_yard_deviation=int(cfg.get("plc.cross_yard_deviation", 0x01)),
            cross_yard_flag=int(cfg.get("plc.cross_yard_flag", 1)),
            base_station_mode=str(cfg.get("plc.base_station_mode", "yard_origin")),
            bay_spacing_m=float(cfg.get("plc.bay_spacing_m", 0.0) or 0.0),
        )



        self._plc_client: Optional[PlcClient] = None

        if self._plc_enabled:

            self._plc_client = PlcClient(

                host=plc_host,

                port=plc_port,

                reconnect_interval=reconnect_interval,

                on_plc_frame=self._on_plc_frame,

            )

        else:

            logger.info("PLC 已禁用（plc.enabled=false），跳过 TCP 连接")



        self._target_rate_hz = cfg.get("plc.frame_rate_hz", 10)

        self._send_interval = 1.0 / self._target_rate_hz



        self._latest_result: Optional[PositionResult] = None

        self._result_lock = threading.Lock()

        self._plc_lock = threading.Lock()



        self._send_thread: Optional[threading.Thread] = None

        self._stop_event = threading.Event()



        self._bus.subscribe(EventBus.TOPIC_POSITION_RESULT, self._on_position_result)



        logger.info(

            "BridgeNode 初始化完成，协议 V1.0.0，目标帧率 %s Hz",

            self._target_rate_hz,

        )



    def start(self) -> None:

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

        logger.info("BridgeNode 停止中...")

        self._stop_event.set()

        if self._send_thread:

            self._send_thread.join(timeout=3.0)

        if self._plc_client:

            self._plc_client.stop()

            self._plc_client.join(timeout=3.0)

        logger.info("BridgeNode 已停止")



    def _on_position_result(self, result: PositionResult) -> None:

        with self._result_lock:

            self._latest_result = result



    def _on_plc_frame(self, frame: PlcToGnssFrame) -> None:

        """PLC 下行：回显对端心跳，缓存最近一帧供调试/后续联动。"""

        with self._plc_lock:

            self._plc_ctx.peer_heartbeat = frame.heartbeat

            self._plc_ctx.last_plc = frame

            if frame.rtg_id:

                self._plc_ctx.vehicle_id = frame.rtg_id.strip() or self._plc_ctx.vehicle_id

        logger.debug(

            "PLC 下行 hb=%d seq=%d yard=%s gantry_mm=%d trolley_mm=%d",

            frame.heartbeat,

            frame.message_seq,

            frame.yard_code,

            frame.gantry_pos_mm,

            frame.trolley_pos_mm,

        )



    def _send_loop(self) -> None:

        logger.info("BridgeNode 周期发送线程启动，间隔 %.3f s", self._send_interval)

        next_send_time = time.time()



        while not self._stop_event.is_set():

            now = time.time()

            if now < next_send_time:

                time.sleep(max(0.001, next_send_time - now))

                continue



            with self._result_lock:

                result = self._latest_result



            if result is None or not self._plc_client:

                next_send_time += self._send_interval

                continue



            try:

                with self._plc_lock:

                    ctx = self._plc_ctx

                    if ctx.bay_spacing_m <= 0:

                        ctx.bay_spacing_m = result.bay_spacing_m

                    frame = encode_gnss_to_plc(result, ctx)

                self._plc_client.send(frame)

            except Exception as exc:

                logger.exception("编码/发送帧失败: %s", exc)



            next_send_time += self._send_interval



    @property

    def is_plc_connected(self) -> bool:

        if not self._plc_client:

            return False

        return self._plc_client.is_connected



    def get_stats(self) -> dict:

        if not self._plc_client:

            return {

                "sent": 0,

                "recv": 0,

                "dropped": 0,

                "errors": 0,

                "target_rate_hz": self._target_rate_hz,

            }

        stats = self._plc_client.stats

        stats["target_rate_hz"] = self._target_rate_hz

        return stats


