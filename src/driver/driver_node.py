"""
驱动节点：driver_node
负责编排所有接收机线程（GNSS x 3 + IMU x 1）的生命周期管理。
大车可为 TCP 客户端连 EVK；小车保持 UDP 监听。
全局一份 NTRIP，RTCM 同口转发给两台大车 EVK。
"""
from typing import List, Optional

from src.utils.config_loader import get_config
from src.driver.gnss_receiver import GnssReceiver
from src.driver.imu_receiver import ImuReceiver
from src.driver.ntrip_client import NtripClient

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
        self._ntrip: Optional[NtripClient] = None
        self._by_role: dict = {}
        self._running = False

        for key in ("receiver_a1", "receiver_a2", "receiver_b"):
            host = cfg.get(f"gnss.{key}.host", "0.0.0.0")
            port = cfg.get(f"gnss.{key}.port", 9001)
            role = cfg.get(f"gnss.{key}.role", key)
            transport = cfg.get(f"gnss.{key}.transport", "udp")
            reconnect = cfg.get(f"gnss.{key}.reconnect_interval_s", 3)
            rcv = GnssReceiver(
                receiver_id=role,
                host=host,
                port=port,
                transport=transport,
                reconnect_interval_s=reconnect,
            )
            self._receivers.append(rcv)
            self._by_role[role] = rcv

        imu_host = cfg.get("imu.host", "0.0.0.0")
        imu_port = cfg.get("imu.port", 9010)
        self._imu_receiver = ImuReceiver(host=imu_host, port=imu_port)

        self._init_ntrip_from_cfg(cfg)

    def _init_ntrip_from_cfg(self, cfg) -> None:
        enabled = bool(cfg.get("ntrip.enabled", False))
        if not enabled:
            self._ntrip = None
            return
        self._ntrip = NtripClient(
            host=str(cfg.get("ntrip.caster_host", "")),
            port=int(cfg.get("ntrip.caster_port", 2101)),
            mountpoint=str(cfg.get("ntrip.mountpoint", "")),
            username=str(cfg.get("ntrip.username", "")),
            password=str(cfg.get("ntrip.password", "")),
            reconnect_interval_s=float(cfg.get("ntrip.reconnect_interval_s", 5)),
            gga_interval_s=float(cfg.get("ntrip.gga_interval_s", 10)),
            on_rtcm=self._fanout_rtcm,
            gga_provider=self._provide_ntrip_gga,
        )

    def _provide_ntrip_gga(self) -> Optional[str]:
        """电气侧（左）主天线 GGA，供 NTRIP/VRS 上报。"""
        left = self._by_role.get("gantry_a1")
        if left is None:
            return None
        return left.get_latest_gga_nmea()

    def _fanout_rtcm(self, data: bytes) -> None:
        """全局一份差分 → 两台大车 EVK TCP。"""
        for role in ("gantry_a1", "gantry_a2"):
            rcv = self._by_role.get(role)
            if rcv is not None:
                rcv.inject_rtcm(data)

    def apply_gnss_layout(self, layout: dict, ntrip: Optional[dict] = None) -> None:
        """
        热更新 EVK 地址 / NTRIP（来自车辆配置 MQTT）。
        TCP 接收机会断线重连到新地址；UDP 仅更新字段（端口变更需重启中控）。
        """
        if not isinstance(layout, dict):
            layout = {}

        def _apply_side(role: str, host_key: str, port_key: str, transport_key: str) -> None:
            rcv = self._by_role.get(role)
            if rcv is None:
                return
            host = layout.get(host_key)
            port = layout.get(port_key)
            transport = layout.get(transport_key)
            kwargs = {}
            if host is not None:
                kwargs["host"] = str(host)
            if port is not None:
                kwargs["port"] = int(port)
            if transport is not None:
                kwargs["transport"] = str(transport)
            if kwargs:
                rcv.reconfigure(**kwargs)
                logger.info(
                    "GNSS [%s] 热更新 → transport=%s %s:%s",
                    role,
                    rcv.transport,
                    rcv.host,
                    rcv.port,
                )

        _apply_side("gantry_a1", "left_host", "left_port", "left_transport")
        _apply_side("gantry_a2", "right_host", "right_port", "right_transport")
        _apply_side("trolley", "trolley_host", "trolley_port", "trolley_transport")

        if isinstance(ntrip, dict):
            enabled = bool(ntrip.get("enabled", False))
            if enabled:
                if self._ntrip is None:
                    self._ntrip = NtripClient(
                        host=str(ntrip.get("caster_host", "")),
                        port=int(ntrip.get("caster_port", 2101)),
                        mountpoint=str(ntrip.get("mountpoint", "")),
                        username=str(ntrip.get("username", "")),
                        password=str(ntrip.get("password", "")),
                        gga_interval_s=float(ntrip.get("gga_interval_s", 10) or 10),
                        on_rtcm=self._fanout_rtcm,
                        gga_provider=self._provide_ntrip_gga,
                    )
                    if self._running:
                        self._start_ntrip()
                    else:
                        logger.info("NTRIP 已按云端配置创建（待 DriverNode.start）")
                else:
                    self._ntrip.set_gga_provider(self._provide_ntrip_gga)
                    self._ntrip.reconfigure(
                        host=ntrip.get("caster_host"),
                        port=ntrip.get("caster_port"),
                        mountpoint=ntrip.get("mountpoint"),
                        username=ntrip.get("username"),
                        password=ntrip.get("password"),
                        gga_interval_s=ntrip.get("gga_interval_s"),
                    )
                    logger.info("NTRIP 参数已热更新并重连")
            elif self._ntrip is not None:
                self._ntrip.stop()
                self._ntrip = None
                logger.info("NTRIP 已按配置关闭")

    def _start_ntrip(self) -> None:
        """启动 NTRIP；已在跑则跳过（线程不可二次 start）。"""
        if self._ntrip is None:
            return
        if self._ntrip.is_alive():
            return
        # 已 start 过且已退出：需新建实例（Python 线程只能 start 一次）
        if self._ntrip.ident is not None:
            logger.warning("NTRIP 线程已结束，跳过二次 start（请热更新重建）")
            return
        self._ntrip.start()
        logger.info("  → NTRIP 全局差分转发已启动")

    def start(self) -> None:
        """启动所有接收机线程。"""
        self._running = True
        logger.info(
            f"DriverNode 启动，共 {len(self._receivers)} 台 GNSS 接收机 + 1 台 IMU"
        )
        for rcv in self._receivers:
            logger.info(
                f"  → GNSS [{rcv.receiver_id}] {rcv.transport.upper()} "
                f"{rcv.host}:{rcv.port}"
            )
            rcv.start()
        if self._imu_receiver:
            self._imu_receiver.start()
        self._start_ntrip()

    def stop(self) -> None:
        """停止所有接收机线程。"""
        logger.info("DriverNode 停止中...")
        self._running = False
        if self._ntrip is not None:
            self._ntrip.stop()
        for rcv in self._receivers:
            rcv.stop()
        if self._imu_receiver:
            self._imu_receiver.stop()
        for rcv in self._receivers:
            rcv.join(timeout=3.0)
        if self._imu_receiver:
            self._imu_receiver.join(timeout=3.0)
        if self._ntrip is not None:
            self._ntrip.join(timeout=3.0)
        logger.info("DriverNode 已停止")
