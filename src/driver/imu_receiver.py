"""
驱动模块：IMU UDP 监听
解析 IMU 二进制或文本数据包，发布 ImuRaw 到 EventBus。

协议约定（可根据实际硬件修改 _parse_imu_packet）：
  二进制包格式（小端，共 32 字节）：
    [0:4]   float32  timestamp（秒，设备内部）
    [4:8]   float32  gyro_x  (rad/s)
    [8:12]  float32  gyro_y  (rad/s)
    [12:16] float32  gyro_z  (rad/s)
    [16:20] float32  accel_x (m/s²)
    [20:24] float32  accel_y (m/s²)
    [24:28] float32  accel_z (m/s²)
    [28:32] float32  temperature (°C)
"""

import socket
import struct
import threading
import time

from src.core.event_bus import get_bus, EventBus
from src.models.imu_data import ImuRaw
from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

_IMU_PACKET_SIZE = 32       # 期望的二进制包大小
_IMU_PACKET_FMT = "<8f"     # 小端，8 个 float32


class ImuReceiver(threading.Thread):
    """
    IMU 数据接收线程。
    支持二进制协议，收到数据后直接发布到 EventBus。
    """

    def __init__(self, host: str, port: int, bus: EventBus = None):
        super().__init__(name="ImuReceiver", daemon=True)
        self.host = host
        self.port = port
        self._bus = bus or get_bus()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(1.0)
        try:
            sock.bind((self.host, self.port))
            logger.info(f"IMU 接收线程启动: {self.host}:{self.port}")

            while not self._stop_event.is_set():
                try:
                    data, _ = sock.recvfrom(64)
                except socket.timeout:
                    continue
                except OSError as e:
                    logger.error(f"IMU Socket 错误: {e}")
                    break

                imu = _parse_imu_packet(data)
                if imu:
                    self._bus.publish(EventBus.TOPIC_IMU_RAW, imu)
        finally:
            sock.close()
            logger.info("IMU 接收线程已关闭")


def _parse_imu_packet(data: bytes) -> ImuRaw | None:
    """
    解析 IMU 二进制数据包。

    :param data: 原始字节
    :return: ImuRaw 实例，或 None（包不合法）
    """
    if len(data) < _IMU_PACKET_SIZE:
        logger.debug(f"IMU 数据包长度不足: {len(data)} bytes")
        return None

    try:
        fields = struct.unpack_from(_IMU_PACKET_FMT, data, 0)
        # fields: (ts, gx, gy, gz, ax, ay, az, temp)
        return ImuRaw(
            timestamp=time.time(),   # 使用系统时间，设备时间仅供参考
            gyro_x=fields[1],
            gyro_y=fields[2],
            gyro_z=fields[3],
            accel_x=fields[4],
            accel_y=fields[5],
            accel_z=fields[6],
            temperature=fields[7],
        )
    except struct.error as e:
        logger.debug(f"IMU 包解析失败: {e}")
        return None

