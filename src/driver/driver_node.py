"""
驱动节点：driver_node
负责编排所有接收机线程（GNSS x 3 + IMU x 1）的生命周期管理。
本身不做数据处理，仅负责启停底层驱动线程。
"""
import logging
from typing import List
from src.utils.config_loader import get_config
from src.driver.gnss_receiver import GnssReceiver
from src.driver.imu_receiver import ImuReceiver

from src.utils.enhanced_logger import EnhancedLogger  
logger = EnhancedLogger.get_logger(__name__)

class DriverNode:
    """
    驱动节点：管理所有底层硬件通信线程。
    调用 start() 后各接收机线程开始运行（daemon 线程）；
    调用 stop() 后依次停止所有线程。
    """
    def __init__(self):
        cfg = get_config()
        self._receivers: List[GnssReceiver] = []
        self._imu_receiver = None
        # 初始化三台 GNSS 接收机
        for key in ("receiver_a1", "receiver_a2", "receiver_b"):
            host = cfg.get(f"gnss.{key}.host", "0.0.0.0")
            port = cfg.get(f"gnss.{key}.port", 9001)
            role = cfg.get(f"gnss.{key}.role", key)
            self._receivers.append(GnssReceiver(
                receiver_id=role,
                host=host,
                port=port,
            ))
        # 初始化 IMU 接收
        imu_host = cfg.get("imu.host", "0.0.0.0")
        imu_port = cfg.get("imu.port", 9002)
        self._imu_receiver = ImuReceiver(host=imu_host, port=imu_port)
    
    def start(self) -> None:
        """启动所有接收机线程。"""
        logger.info(f"DriverNode 启动，共 {len(self._receivers)} 台 GNSS 接收机 + 1 台 IMU")
        for rcv in self._receivers:
            logger.info(
                f"  → GNSS [{rcv.receiver_id}] UDP {rcv.host}:{rcv.port}"
            )
            rcv.start()
        if self._imu_receiver:
            self._imu_receiver.start()
    
    def stop(self) -> None:
        """停止所有接收机线程。"""
        logger.info("DriverNode 停止中...")
        for rcv in self._receivers:
            rcv.stop()
        if self._imu_receiver:
            self._imu_receiver.stop()
        for rcv in self._receivers:
            rcv.join(timeout=3.0)
        if self._imu_receiver:
            self._imu_receiver.join(timeout=3.0)
        logger.info("DriverNode 已停止")

