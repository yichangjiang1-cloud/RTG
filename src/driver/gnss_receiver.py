"""
驱动模块：GNSS 接收机 UDP 监听
每台接收机对应一个 GnssReceiver 实例，运行在独立线程中。
收到 UDP 数据包后解析 NMEA 语句，组装 GnssRaw 并通过 EventBus 发布。
"""

import math
import socket
import sys
import threading
import time
from typing import Optional

from src.core.event_bus import get_bus, EventBus
from src.models.gnss_data import GnssRaw, FixQuality
from src.driver.nmea_parser import parse_sentence
from src.driver.gnss_raw_file_logger import create_gnss_raw_file_logger
from src.driver.rtcm_inject import RtcmOutQueue, RtcmPacer, flush_rtcm_drain
from src.solver.coordinate_transform import wgs84_to_ecef
from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

# UDP 接收缓冲区大小
_UDP_BUFFER_SIZE = 4096
# 接收机数据超时告警默认阈值（秒）；可被 gnss.offline_timeout_s 覆盖
_DEFAULT_DATA_TIMEOUT_S = 0.5
# 未接设备通道：超时提示最小间隔（秒，DEBUG）
_IDLE_CHANNEL_WARN_INTERVAL_S = 60.0
# 曾在线后断流：WARNING 最小间隔（秒）
_ACTIVE_CHANNEL_WARN_INTERVAL_S = 10.0
# 接收机状态 INFO 摘要最小间隔（秒）；定位质量变化时立即打一条
_INFO_SUMMARY_INTERVAL_S = 60.0
# 双天线基线水平距离合理范围（米），用于跨包识别副天线 GGA
_SECONDARY_MIN_SEPARATION_M = 2.0
_SECONDARY_MAX_SEPARATION_M = 80.0


def _configure_udp_listen_socket(sock: socket.socket) -> None:
    """
    配置 UDP 监听 socket。
    Windows 上禁止 SO_REUSEADDR：否则 9003 等端口可被多进程同时绑定，
    发往本机的模拟包可能被其它 socket 抢走，trolley 线程一直收不到数据。
    """
    if sys.platform == "win32":
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def _summarize_nmea_types(text: str) -> str:
    """从原始文本提取语句类型摘要，如 GGA+GGA2+RMC+PASHR。"""
    types = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            if "BESTPOS2" in line.upper()[:12]:
                types.append("BESTPOS2A")
            continue
        if not line.startswith("$"):
            continue
        head = line[1:].split(",", 1)[0].upper()
        if head.startswith("PSAT") and "GGA2" in line.upper():
            types.append("GGA2")
        elif "GGA" in head:
            types.append("GGA")
        elif "RMC" in head:
            types.append("RMC")
        elif "VTG" in head:
            types.append("VTG")
        elif "HDT" in head:
            types.append("HDT")
        elif "PASHR" in head or "HPR" in head:
            types.append("HPR" if "HPR" in head else "PASHR")
        else:
            types.append(head.split("*")[0][:8])
    return "+".join(types) if types else "?"


def _gnss_raw_log_echo_console() -> bool:
    """是否将每条 NMEA 实时打印到控制台（不写 rtg.log）。"""
    try:
        from src.utils.config_loader import get_config

        return bool(get_config().get("gnss.raw_log.echo_console", False))
    except Exception:
        return False


def _iter_nmea_lines(text: str):
    """从 UDP 文本中提取 NMEA / 注释行。"""
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if line.startswith("$") or line.startswith("#"):
            yield line


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
        transport: str = "udp",
        reconnect_interval_s: float = 3.0,
        connect_timeout_s: float = 5.0,
        offline_timeout_s: float = _DEFAULT_DATA_TIMEOUT_S,
    ):
        """
        :param receiver_id: 接收机标识，如 "gantry_a1"
        :param host:        UDP=本机绑定；TCP=EVK 服务端 IP
        :param port:        UDP=监听端口；TCP=EVK 服务端口
        :param transport:   "udp" | "tcp"
        :param bus:         事件总线（默认使用全局单例）
        :param offline_timeout_s: 无数据超过该秒数打超时告警
        """
        super().__init__(name=f"GnssReceiver-{receiver_id}", daemon=True)
        self.receiver_id = receiver_id
        self.host = host
        self.port = int(port)
        self.transport = (transport or "udp").strip().lower()
        self._reconnect_interval_s = max(1.0, float(reconnect_interval_s))
        self._connect_timeout_s = max(1.0, float(connect_timeout_s))
        self._data_timeout_s = max(0.1, float(offline_timeout_s))
        self._bus = bus or get_bus()
        self._stop_event = threading.Event()
        self._endpoint_lock = threading.Lock()

        # 当前帧缓存（在一个包的多条语句间累积状态）
        self._frame: GnssRaw = GnssRaw(receiver_id=receiver_id)
        self._last_data_time: float = 0.0
        self._ever_received: bool = False
        self._last_timeout_warn: float = 0.0
        self._peer_addr: Optional[tuple] = None
        # 副天线最近一次有效 GGA（跨 UDP 包保留，供「一行一 GGA」类接收机/模拟器）
        self._cached_antenna2: Optional[dict] = None
        # 单 GGA 包交替：主 → 副 → 主 …
        self._expect_secondary_gga: bool = False
        self._publish_count: int = 0
        self._last_info_fix_quality: Optional[FixQuality] = None
        self._last_info_summary_time: float = 0.0
        self._echo_nmea_console = _gnss_raw_log_echo_console()
        self._raw_file_logger = create_gnss_raw_file_logger(receiver_id, port)

        # TCP：活动套接字；RTCM 只入队，由收包循环点写
        self._tcp_sock: Optional[socket.socket] = None
        self._tcp_send_lock = threading.Lock()
        self._rtcm_q = RtcmOutQueue()
        self._rtcm_pacer = RtcmPacer()
        self._tcp_line_buf = ""
        self._tcp_connected = False
        self._udp_sock: Optional[socket.socket] = None
        # 地址/协议变更世代号：UDP/TCP 循环检测后换绑或切模式
        self._endpoint_gen: int = 0
        # 最近一次有效主天线 GGA（发布后 _frame 会清空，NTRIP 必须读这里）
        self._last_ntrip_gga: Optional[str] = None
        self._ntrip_gga_lock = threading.Lock()

    @property
    def is_tcp_connected(self) -> bool:
        return self.transport == "tcp" and self._tcp_connected

    def reconfigure(
        self,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        transport: Optional[str] = None,
        reconnect_interval_s: Optional[float] = None,
    ) -> bool:
        """热更新对端；地址/协议变化时断开 TCP 或关闭 UDP 以换绑。未变化返回 False。"""
        changed = False
        with self._endpoint_lock:
            if host is not None and str(host) != self.host:
                self.host = str(host)
                changed = True
            if port is not None and int(port) != self.port:
                self.port = int(port)
                changed = True
            if transport is not None:
                t = str(transport).strip().lower()
                if t != self.transport:
                    self.transport = t
                    changed = True
            if reconnect_interval_s is not None:
                self._reconnect_interval_s = max(1.0, float(reconnect_interval_s))
            if changed:
                self._endpoint_gen += 1
        if changed:
            self._close_tcp()
            self._close_udp()
        return changed

    @staticmethod
    def _tcp_host_disabled(host: str) -> bool:
        """未接该侧 EVK 时：host 留空或填 disabled/none/-，不发起连接。"""
        h = (host or "").strip().lower()
        return (not h) or h in ("0.0.0.0", "-", "none", "disabled", "n/a")

    def inject_rtcm(self, data: bytes) -> bool:
        """RTCM 入队，由 TCP 收包线程在可写时点写。NTRIP 线程不得 sendall。"""
        if self.transport != "tcp" or not data:
            return False
        if getattr(self, "_tcp_session_rx", 0) <= 0:
            return False
        if self._tcp_sock is None or not self._tcp_connected:
            return False
        self._rtcm_q.put(data)
        return True

    def _flush_rtcm(self, sock: socket.socket) -> None:
        try:
            flush_rtcm_drain(sock, self._rtcm_q, self._rtcm_pacer)
        except OSError as e:
            logger.debug("[%s] RTCM 点写失败: %s", self.receiver_id, e)
            raise ConnectionError(str(e)) from e

    def _close_tcp(self) -> None:
        self._tcp_connected = False
        sock = self._tcp_sock
        self._tcp_sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _close_udp(self) -> None:
        sock = self._udp_sock
        self._udp_sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def get_latest_gga_nmea(self) -> Optional[str]:
        """返回最近一次有效主天线 GGA，供 NTRIP/VRS 上报。

        注意：``_publish_frame`` 后会重置 ``_frame``，不能从当前帧拼 GGA，
        否则会得到 lat/lon=0 的无效句，caster 表现为「已连接但未上传 GGA」。
        """
        with self._ntrip_gga_lock:
            return self._last_ntrip_gga

    @staticmethod
    def _nmea_for_ntrip(raw: str) -> str:
        """部分国内 caster 只认 $GPGGA，将 $GNxGA 归一为 $GPGGA 并重算校验。"""
        line = (raw or "").strip()
        if not line.startswith("$") or "*" not in line:
            return line
        body, _, _ = line[1:].partition("*")
        upper = body.upper()
        if upper.startswith("GN") and "GGA" in upper[:6]:
            body = "GP" + body[2:]
        csum = 0
        for ch in body:
            csum ^= ord(ch)
        return f"${body}*{csum:02X}"

    def _cache_ntrip_gga(self, parsed: dict) -> None:
        """缓存有效主天线 GGA；无定位质量则不提供给 NTRIP。"""
        if int(parsed.get("fix_quality", 0)) <= 0:
            return
        lat = parsed.get("lat")
        lon = parsed.get("lon")
        if lat is None or lon is None:
            return
        if abs(float(lat)) < 1e-8 and abs(float(lon)) < 1e-8:
            return
        raw = (parsed.get("raw") or "").strip()
        if not raw.startswith("$"):
            return
        line = self._nmea_for_ntrip(raw)
        with self._ntrip_gga_lock:
            self._last_ntrip_gga = line

    def _log_raw_packet(self, data: bytes, addr: tuple, text: str) -> None:
        """DEBUG：单行摘要；echo_console 时逐条 NMEA 打印到控制台。"""
        host, port = addr[0], addr[1]
        if self._peer_addr != addr:
            self._peer_addr = addr
            mode = self.transport.upper()
            logger.info(
                f"[{self.receiver_id}] {mode} 数据源 {host}:{port}"
            )
        summary = _summarize_nmea_types(text)
        logger.debug(
            f"[{self.receiver_id}] {self.transport.upper()} {len(data)}B "
            f"from {host}:{port} | {summary}"
        )
        if self._echo_nmea_console:
            for line in _iter_nmea_lines(text):
                print(f"[{self.receiver_id}] {line}", flush=True)

    def _log_published_frame(self) -> None:
        """发布帧日志：逐帧 DEBUG；INFO 仅在定位质量变化或周期摘要。"""
        f = self._frame
        hdg = f.heading if f.heading is not None else float("nan")
        ant2 = ""
        if f.has_antenna2:
            ant2 = (
                f" | A2=({f.antenna2_latitude:.6f},{f.antenna2_longitude:.6f})"
            )
        self._publish_count += 1
        hdg_s = f"{hdg:.1f}" if f.heading is not None else "—"
        rtk_tag = "RTK" if f.is_rtk else f.fix_quality.name
        logger.debug(
            f"[{self.receiver_id}] #{self._publish_count} "
            f"fix={rtk_tag}({int(f.fix_quality)}) sats={f.num_satellites} "
            f"({f.latitude:.6f},{f.longitude:.6f}) "
            f"spd={f.speed_mps:.2f} hdg={hdg_s}°{ant2}"
        )

        now = time.time()
        fix_changed = self._last_info_fix_quality != f.fix_quality
        due_summary = now - self._last_info_summary_time >= _INFO_SUMMARY_INTERVAL_S
        if fix_changed or due_summary:
            self._last_info_fix_quality = f.fix_quality
            self._last_info_summary_time = now
            if fix_changed:
                reason = "定位质量变化"
            else:
                reason = f"{_INFO_SUMMARY_INTERVAL_S:.0f}s 摘要"
            logger.info(
                f"[{self.receiver_id}] {reason} | #{self._publish_count} "
                f"fix={rtk_tag} sats={f.num_satellites} "
                f"({f.latitude:.6f},{f.longitude:.6f})"
            )

    def stop(self) -> None:
        """通知接收线程停止。"""
        self._stop_event.set()
        self._close_tcp()
        self._close_udp()

    def run(self) -> None:
        """线程主循环：可在 TCP / UDP 间热切换（由 reconfigure 改 transport）。"""
        try:
            while not self._stop_event.is_set():
                if self.transport == "tcp":
                    self._run_tcp_client()
                else:
                    self._run_udp_listen()
                if self._stop_event.is_set():
                    break
                # 协议已切换：短暂休眠后进入另一模式
                self._stop_event.wait(0.2)
        finally:
            if self._raw_file_logger:
                self._raw_file_logger.close()
            self._close_tcp()
            self._close_udp()

    def _run_udp_listen(self) -> None:
        """UDP 监听；host/port/transport 变更时换绑并返回。"""
        while not self._stop_event.is_set():
            if self.transport != "udp":
                return
            with self._endpoint_lock:
                host, port, gen = self.host, self.port, self._endpoint_gen

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _configure_udp_listen_socket(sock)
            sock.settimeout(1.0)
            self._udp_sock = sock
            try:
                sock.bind((host, port))
                logger.info("[%s] UDP 监听 %s:%s", self.receiver_id, host, port)
                if self._echo_nmea_console:
                    logger.info(
                        "[%s] NMEA 实时回显已开启 (gnss.raw_log.echo_console=true)",
                        self.receiver_id,
                    )

                while not self._stop_event.is_set():
                    if self.transport != "udp" or self._endpoint_gen != gen:
                        break
                    try:
                        data, addr = sock.recvfrom(_UDP_BUFFER_SIZE)
                    except socket.timeout:
                        now = time.time()
                        if (
                            self._last_data_time > 0
                            and now - self._last_data_time > self._data_timeout_s
                        ):
                            self._log_timeout_if_needed()
                            if self._raw_file_logger:
                                self._raw_file_logger.end_session_if_idle(now)
                        continue
                    except OSError as e:
                        if self._stop_event.is_set() or self._endpoint_gen != gen:
                            break
                        logger.error("[%s] Socket 错误: %s", self.receiver_id, e)
                        break

                    self._ever_received = True
                    self._last_data_time = time.time()
                    try:
                        packet_text = data.decode("ascii", errors="ignore")
                    except UnicodeDecodeError:
                        logger.warning(
                            "[%s] UDP 非 ASCII，%sB hex=%s",
                            self.receiver_id,
                            len(data),
                            data.hex(),
                        )
                        if self._raw_file_logger:
                            self._raw_file_logger.write_packet(data, addr, time.time())
                        continue
                    if self._raw_file_logger:
                        self._raw_file_logger.write_packet(data, addr, time.time())
                    self._log_raw_packet(data, addr, packet_text)
                    self._process_packet(packet_text)
            except OSError as e:
                logger.error(
                    "[%s] UDP bind %s:%s 失败: %s", self.receiver_id, host, port, e
                )
                if self._stop_event.wait(self._reconnect_interval_s):
                    return
            finally:
                if self._udp_sock is sock:
                    self._udp_sock = None
                try:
                    sock.close()
                except OSError:
                    pass
                logger.info("[%s] UDP 监听已关闭 %s:%s", self.receiver_id, host, port)

            if self.transport != "udp" or self._stop_event.is_set():
                return

    def _run_tcp_client(self) -> None:
        """作为客户端连接 EVK TCP 服务端，断线自动重连；transport 切走则返回。"""
        logger.info(
            "[%s] TCP 客户端模式 → %s:%s（断线每 %.0fs 重试）",
            self.receiver_id,
            self.host,
            self.port,
            self._reconnect_interval_s,
        )
        while not self._stop_event.is_set():
            if self.transport != "tcp":
                return
            with self._endpoint_lock:
                host, port = self.host, self.port
            if self._tcp_host_disabled(host):
                logger.info(
                    "[%s] TCP 未配置有效 EVK 地址（host=%r），暂停连接；"
                    "接好设备后在前端填写 IP",
                    self.receiver_id,
                    host,
                )
                if self._stop_event.wait(30.0):
                    return
                continue
            try:
                sock = socket.create_connection(
                    (host, port), timeout=self._connect_timeout_s
                )
            except OSError as e:
                logger.warning(
                    "[%s] TCP 连接 %s:%s 失败: %s，%.0fs 后重试",
                    self.receiver_id,
                    host,
                    port,
                    e,
                    self._reconnect_interval_s,
                )
                if self._stop_event.wait(self._reconnect_interval_s):
                    return
                continue

            sock.settimeout(1.0)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
            except OSError:
                pass
            self._tcp_sock = sock
            self._tcp_connected = True
            self._tcp_line_buf = ""
            self._tcp_session_rx = 0
            self._rtcm_q.clear()
            t0 = time.time()
            logger.info("[%s] ✓ TCP 已连接 EVK %s:%s", self.receiver_id, host, port)
            empty_rst = False
            try:
                self._tcp_recv_loop(sock, host, port)
            except Exception as e:
                dt = time.time() - t0
                empty_rst = self._tcp_session_rx == 0
                if empty_rst:
                    logger.warning(
                        "[%s] TCP 会话异常（连接仅维持 %.1fs 且未收到任何字节）: %s — "
                        "对端主动 RST：上一连接未释放、或刚写入的 RTCM 被 NMEA 口拒绝。"
                        "将拉长重连间隔，避免把接收机打满",
                        self.receiver_id,
                        dt,
                        e,
                    )
                else:
                    logger.warning("[%s] TCP 会话异常: %s", self.receiver_id, e)
            finally:
                self._close_tcp()
                logger.info("[%s] TCP 已断开 %s:%s", self.receiver_id, host, port)

            if self.transport != "tcp":
                return
            wait_s = self._reconnect_interval_s
            if empty_rst:
                wait_s = max(wait_s, 15.0)
            if self._stop_event.wait(wait_s):
                return

    def _tcp_recv_loop(self, sock: socket.socket, host: str, port: int) -> None:
        """收包循环。"""
        while not self._stop_event.is_set():
            try:
                data = sock.recv(_UDP_BUFFER_SIZE)
            except socket.timeout:
                now = time.time()
                if (
                    self._last_data_time > 0
                    and now - self._last_data_time > self._data_timeout_s
                ):
                    self._log_timeout_if_needed()
                    if self._raw_file_logger:
                        self._raw_file_logger.end_session_if_idle(now)
                self._flush_rtcm(sock)
                continue
            except OSError as e:
                raise ConnectionError(str(e)) from e
            if not data:
                raise ConnectionError("对端关闭连接")

            self._tcp_session_rx = getattr(self, "_tcp_session_rx", 0) + len(data)
            self._ever_received = True
            self._last_data_time = time.time()
            addr = (host, port)
            if self._raw_file_logger:
                self._raw_file_logger.write_packet(data, addr, time.time())

            # 二进制 RTCM 可能混入；只把可打印 ASCII 送入 NMEA 行缓冲
            try:
                text = data.decode("ascii", errors="ignore")
            except UnicodeDecodeError:
                self._flush_rtcm(sock)
                continue
            if not text:
                self._flush_rtcm(sock)
                continue
            self._log_raw_packet(data, addr, text)
            self._tcp_line_buf += text
            while "\n" in self._tcp_line_buf or "\r" in self._tcp_line_buf:
                for sep in ("\r\n", "\n", "\r"):
                    if sep in self._tcp_line_buf:
                        line, _, rest = self._tcp_line_buf.partition(sep)
                        self._tcp_line_buf = rest
                        line = line.strip()
                        # $NMEA 与和芯 #BESTPOS2A
                        if line.startswith("$") or line.startswith("#"):
                            self._process_packet(line)
                        break
                else:
                    break
            # 防止缓冲无限增长
            if len(self._tcp_line_buf) > 8192:
                self._tcp_line_buf = self._tcp_line_buf[-2048:]
            self._flush_rtcm(sock)

    def _log_timeout_if_needed(self) -> None:
        now = time.time()
        interval = (
            _ACTIVE_CHANNEL_WARN_INTERVAL_S
            if self._ever_received
            else _IDLE_CHANNEL_WARN_INTERVAL_S
        )
        if now - self._last_timeout_warn < interval:
            return
        self._last_timeout_warn = now
        mode = "TCP" if self.transport == "tcp" else "UDP"
        endpoint = (
            f"{self.host}:{self.port}"
            if self.transport == "tcp"
            else f"监听 :{self.port}"
        )
        msg = (
            f"[{self.receiver_id}] {self._data_timeout_s:g}s 无 {mode} 数据 "
            f"({endpoint})"
        )
        if self._ever_received:
            logger.warning(msg)
        else:
            logger.debug(msg + " — 未接接收机时可忽略")

    def _process_packet(self, data: str | bytes) -> None:
        """
        处理一个数据包 / 一行 NMEA。
        一个包内可能有多条 NMEA 语句，全部解析后合并成一帧发布。
        """
        if isinstance(data, bytes):
            try:
                text = data.decode("ascii", errors="ignore")
            except UnicodeDecodeError:
                return
        else:
            text = data

        sentences = list(_iter_nmea_lines(text))
        parsed = [parse_sentence(s) for s in sentences]
        parsed = [r for r in parsed if r is not None]
        gga_total = sum(1 for r in parsed if r.get("type") == "GGA")

        frame_updated = False
        gga_index = 0

        for result in parsed:
            msg_type = result.get("type")
            if msg_type == "GGA":
                self._merge_gga(result, gga_index, single_gga_in_packet=(gga_total == 1))
                gga_index += 1
            elif msg_type == "GGA2":
                self._apply_secondary_gga(result)
            else:
                self._merge_into_frame(result)
            frame_updated = True

        if frame_updated and self._frame.is_valid:
            self._publish_frame()

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
        self._cache_ntrip_gga(parsed)

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
        同包 GGA 顺序固定：第 1 条=主天线，第 2 条=副天线。
        大车：GGA[0]=电气房，GGA[1]=柴油机；小车：GGA[0]=C1，GGA[1]=C2。
        单 GGA 包仅更新主天线，副天线沿用跨包缓存。
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

        elif msg_type == "VTG":
            # RMC 优先；无 RMC 速度时用 VTG 补
            if self._frame.speed_mps <= 0.0:
                self._frame.speed_mps = parsed["speed_mps"]
            if self._frame.course_deg == 0.0 and parsed.get("course_deg"):
                self._frame.course_deg = parsed["course_deg"]

        elif self.receiver_id != "trolley" and msg_type == "HDT":
            self._frame.heading = parsed["heading_deg"]

        elif self.receiver_id != "trolley" and msg_type == "ATTITUDE":
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
