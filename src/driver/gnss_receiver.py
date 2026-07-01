"""
驱动模块：GNSS 接收机 UDP 监听
每台接收机对应一个 GnssReceiver 实例，运行在独立线程中。
收到 UDP 数据包后解析 NMEA 语句，组装 GnssRaw 并通过 EventBus 发布。
"""

import math
import socket
import threading
import time
from typing import Optional

from src.core.event_bus import get_bus, EventBus
from src.models.gnss_data import GnssRaw, FixQuality
from src.driver.nmea_parser import parse_sentence
from src.solver.coordinate_transform import wgs84_to_ecef
from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

# UDP 接收缓冲区大小
_UDP_BUFFER_SIZE = 4096
# 接收机数据超时告警阈值（秒）
_DATA_TIMEOUT_S = 2.0
# 双天线基线水平距离合理范围（米），用于跨包识别副天线 GGA
_SECONDARY_MIN_SEPARATION_M = 2.0
_SECONDARY_MAX_SEPARATION_M = 80.0


class GnssReceiver(threading.Thread):
    """
    
    单台 GNSS 接收机的 UDP 数据接收与解析线程。

    每个 UDP 数据包可能包含多条 NMEA 语句（以 \\r\\n 分隔）。
    解析完成后合并为一帧 GnssRaw，发布到 EventBus。
    """

    def __init__(
        self,
        receiver_id: str,
        host: str,
        port: int,
        bus: Optional[EventBus] = None,
    ):
        """
        :param receiver_id: 接收机标识，如 "gantry_a1"
        :param host:        绑定的本机 IP（接收机向此地址推送 UDP）
        :param port:        监听端口
        :param bus:         事件总线（默认使用全局单例）
        """
        super().__init__(name=f"GnssReceiver-{receiver_id}", daemon=True)
        self.receiver_id = receiver_id
        self.host = host
        self.port = port
        self._bus = bus or get_bus()
        self._stop_event = threading.Event()

        # 当前帧缓存（在一个包的多条语句间累积状态）
        self._frame: GnssRaw = GnssRaw(receiver_id=receiver_id)
        self._last_data_time: float = 0.0
        # 副天线最近一次有效 GGA（跨 UDP 包保留，供「一行一 GGA」类接收机/模拟器）
        self._cached_antenna2: Optional[dict] = None
        # 单 GGA 包交替：主 → 副 → 主 …
        self._expect_secondary_gga: bool = False
        self._publish_count: int = 0

    @staticmethod
    def _preview_packet(text: str, limit: int = 240) -> str:
        """单行预览 NMEA 包内容（便于日志）。"""
        one_line = " | ".join(s.strip() for s in text.splitlines() if s.strip())
        if len(one_line) <= limit:
            return one_line
        return one_line[: limit - 3] + "..."

    def _log_published_frame(self) -> None:
        """发布前打印本帧解析结果（主天线 + 副天线 + 姿态句）。"""
        f = self._frame
        hdg = f.heading if f.heading is not None else float("nan")
        pitch = f.pitch if f.pitch is not None else float("nan")
        roll = f.roll if f.roll is not None else float("nan")
        ant2 = ""
        if f.has_antenna2:
            ant2 = (
                f", A2=({f.antenna2_latitude:.8f},{f.antenna2_longitude:.8f},"
                f"{f.antenna2_altitude:.3f}m)"
            )
        self._publish_count += 1
        hdg_s = f"{hdg:.2f}" if f.heading is not None else "—"
        pitch_s = f"{pitch:.2f}" if f.pitch is not None else "—"
        roll_s = f"{roll:.2f}" if f.roll is not None else "—"
        logger.info(
            f"[{self.receiver_id}] 发布 GNSS #{self._publish_count} | "
            f"fix={f.fix_quality.name} sats={f.num_satellites} | "
            f"A1=({f.latitude:.8f},{f.longitude:.8f},{f.altitude:.3f}m) "
            f"speed={f.speed_mps:.3f}m/s course={f.course_deg:.1f}° | "
            f"NMEA姿态 hdg={hdg_s} pitch={pitch_s} roll={roll_s}{ant2}"
        )

    def stop(self) -> None:
        """通知接收线程停止。"""
        self._stop_event.set()

    def run(self) -> None:
        """线程主循环：持续监听 UDP 并解析数据。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)    # 允许周期检查 stop_event

        try:
            sock.bind((self.host, self.port))
            logger.info(f"[{self.receiver_id}] UDP 监听启动: {self.host}:{self.port}")

            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(_UDP_BUFFER_SIZE)
                except socket.timeout:
                    # 超时检查数据是否中断
                    if (self._last_data_time > 0
                            and time.time() - self._last_data_time > _DATA_TIMEOUT_S):
                        logger.warning(
                            f"[{self.receiver_id}] 超过 {_DATA_TIMEOUT_S:.1f} s 未收到数据"
                        )
                    continue
                except OSError as e:
                    logger.error(f"[{self.receiver_id}] Socket 错误: {e}")
                    break

                self._last_data_time = time.time()
                try:
                    packet_text = data.decode("ascii", errors="ignore")
                except UnicodeDecodeError:
                    continue
                logger.info(
                    f"[{self.receiver_id}] 收到 UDP {len(data)}B: "
                    f"{self._preview_packet(packet_text)}"
                )
                self._process_packet(packet_text)

        finally:
            sock.close()
            logger.info(f"[{self.receiver_id}] UDP 监听已关闭")

    # ------------------------------------------------------------------
    # 内部：数据包处理
    # ------------------------------------------------------------------
    def _process_packet(self, data: str | bytes) -> None:
        """
        处理一个 UDP 数据包。
        一个包内可能有多条 NMEA 语句，全部解析后合并成一帧发布。
        """
        if isinstance(data, bytes):
            try:
                text = data.decode("ascii", errors="ignore")
            except UnicodeDecodeError:
                return
        else:
            text = data

        sentences = [s.strip() for s in text.splitlines() if s.strip()]
        parsed = [parse_sentence(s) for s in sentences]
        parsed = [r for r in parsed if r is not None]
        gga_total = sum(1 for r in parsed if r.get("type") == "GGA")

        frame_updated = False
        gga_index = 0

        for result in parsed:
            if result.get("type") == "GGA":
                self._merge_gga(result, gga_index, single_gga_in_packet=(gga_total == 1))
                gga_index += 1
            else:
                self._merge_into_frame(result)
            frame_updated = True

        # 包内所有语句解析完后，若包含位置信息则发布本帧
        if frame_updated:
            if self._frame.is_valid:
                self._publish_frame()
            else:
                # 数据有效但精度不足，记录调试信息
                if self._frame.fix_quality != FixQuality.INVALID:
                    logger.debug(f"[{self.receiver_id}] 数据精度不足 (fix_quality={self._frame.fix_quality.name}), "
                                 f"需要 RTK 固定解(4) 或 RTK 浮点解(5)")


    @staticmethod
    def _horizontal_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6_371_000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
        return 2 * r * math.asin(math.sqrt(min(1.0, a)))

    def _looks_like_secondary_gga(self, parsed: dict) -> bool:
        """单 GGA 包时，根据与主天线距离或交替顺序判断是否为副天线。"""
        if self._frame.latitude is None or self._frame.longitude is None:
            return self._expect_secondary_gga
        dist = self._horizontal_distance_m(
            self._frame.latitude,
            self._frame.longitude,
            parsed["lat"],
            parsed["lon"],
        )
        if _SECONDARY_MIN_SEPARATION_M <= dist <= _SECONDARY_MAX_SEPARATION_M:
            return True
        if dist < 1.0:
            return False
        return self._expect_secondary_gga

    def _apply_primary_gga(self, parsed: dict) -> None:
        self._frame.timestamp = parsed["timestamp"]
        self._frame.latitude = parsed["lat"]
        self._frame.longitude = parsed["lon"]
        self._frame.altitude = parsed["alt"]
        self._frame.fix_quality = FixQuality(parsed["fix_quality"])
        self._frame.num_satellites = parsed["num_sats"]
        self._frame.hdop = parsed["hdop"]
        self._expect_secondary_gga = True

    def _apply_secondary_gga(self, parsed: dict) -> None:
        self._frame.antenna2_latitude = parsed["lat"]
        self._frame.antenna2_longitude = parsed["lon"]
        self._frame.antenna2_altitude = parsed["alt"]
        self._frame.antenna2_fix_quality = FixQuality(parsed["fix_quality"])
        self._frame.antenna2_num_satellites = parsed["num_sats"]
        self._frame.antenna2_hdop = parsed["hdop"]
        self._cached_antenna2 = parsed
        self._expect_secondary_gga = False

    def _apply_cached_antenna2(self) -> None:
        if self._frame.has_antenna2 or not self._cached_antenna2:
            return
        parsed = self._cached_antenna2
        self._frame.antenna2_latitude = parsed["lat"]
        self._frame.antenna2_longitude = parsed["lon"]
        self._frame.antenna2_altitude = parsed["alt"]
        self._frame.antenna2_fix_quality = FixQuality(parsed["fix_quality"])
        self._frame.antenna2_num_satellites = parsed["num_sats"]
        self._frame.antenna2_hdop = parsed["hdop"]

    def _merge_gga(self, parsed: dict, index: int, *, single_gga_in_packet: bool) -> None:
        """
        同包内第 1 条 GGA→主天线，第 2 条→副天线。
        若每包仅 1 条 GGA（常见日志回放），则按基线距离或主/副交替识别。
        """
        if not single_gga_in_packet:
            if index == 0:
                self._apply_primary_gga(parsed)
            elif index == 1:
                self._apply_secondary_gga(parsed)
            else:
                logger.debug(
                    f"[{self.receiver_id}] 忽略第 {index + 1} 条 GGA（仅保留前两路天线）"
                )
            return

        if self._looks_like_secondary_gga(parsed):
            self._apply_secondary_gga(parsed)
        else:
            self._apply_primary_gga(parsed)

    def _merge_into_frame(self, parsed: dict) -> None:
        """
        将单条 NMEA 解析结果合并到当前帧缓存中。
        不同类型的语句填充 GnssRaw 的不同字段。
        """
        msg_type = parsed.get("type")

        if msg_type == "GGA":
            return

        if msg_type == "RMC":
            self._frame.speed_mps = parsed["speed_mps"]
            self._frame.course_deg = parsed["course_deg"]

        elif msg_type == "HDT":
            self._frame.heading = parsed["heading_deg"]

        elif msg_type == "ATTITUDE":
            self._frame.heading = parsed["heading_deg"]
            self._frame.pitch = parsed.get("pitch_deg")
            self._frame.roll = parsed.get("roll_deg")

    def _publish_frame(self) -> None:
        """
        将当前帧转换为 ECEF 坐标并发布到 EventBus。
        发布后重置帧缓存（保留 receiver_id）。
        """
        self._apply_cached_antenna2()

        # 附加 ECEF 坐标（供 EKF 直接使用）
        ex, ey, ez = wgs84_to_ecef(
            self._frame.latitude,
            self._frame.longitude,
            self._frame.altitude,
        )
        self._frame.ecef_x = ex
        self._frame.ecef_y = ey
        self._frame.ecef_z = ez

        if self._frame.has_antenna2:
            a2x, a2y, a2z = wgs84_to_ecef(
                self._frame.antenna2_latitude,
                self._frame.antenna2_longitude,
                self._frame.antenna2_altitude,
            )
            self._frame.antenna2_ecef_x = a2x
            self._frame.antenna2_ecef_y = a2y
            self._frame.antenna2_ecef_z = a2z

        self._log_published_frame()
        self._bus.publish(EventBus.TOPIC_GNSS_RAW, self._frame)

        # 重置帧缓存
        self._frame = GnssRaw(receiver_id=self.receiver_id)

