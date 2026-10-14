"""
全局 NTRIP 客户端：拉取 RTCM，回调分发给多台 EVK（同口转发）。

多数网络差分（VRS）要求客户端在握手后周期性上报 GGA，否则 caster
会在约 60s 内静默关闭连接且不推送 RTCM。
"""

from __future__ import annotations

import base64
import socket
import threading
import time
from typing import Callable, Optional

from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

OnRtcmCallback = Callable[[bytes], None]
GgaProvider = Callable[[], Optional[str]]


class NtripClient(threading.Thread):
    """
    连接 NTRIP Caster，持续接收 RTCM，经回调转发。
    断线自动重连；握手后立即并周期性上报 GGA。
    """

    def __init__(
        self,
        host: str,
        port: int = 2101,
        mountpoint: str = "",
        username: str = "",
        password: str = "",
        reconnect_interval_s: float = 5.0,
        gga_interval_s: float = 10.0,
        on_rtcm: Optional[OnRtcmCallback] = None,
        gga_provider: Optional[GgaProvider] = None,
    ):
        super().__init__(name="NtripClient", daemon=True)
        self._host = host
        self._port = int(port)
        self._mountpoint = (mountpoint or "").lstrip("/")
        self._username = username or ""
        self._password = password or ""
        self._reconnect_interval_s = max(1.0, float(reconnect_interval_s))
        self._gga_interval_s = max(0.0, float(gga_interval_s))
        self._on_rtcm = on_rtcm
        self._gga_provider = gga_provider

        self._stop_event = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._gga_lock = threading.Lock()
        self._latest_gga: Optional[str] = None
        self._last_gga_sent = 0.0
        self._last_no_gga_warn = 0.0
        self._rtcm_bytes = 0
        self._last_rtcm_log = 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def update_gga(self, gga_sentence: str) -> None:
        """供中控把最近一帧 GGA 喂给 caster（可含或不含换行）。"""
        line = (gga_sentence or "").strip()
        if not line:
            return
        if not line.startswith("$"):
            return
        with self._gga_lock:
            self._latest_gga = line

    def set_gga_provider(self, provider: Optional[GgaProvider]) -> None:
        self._gga_provider = provider

    def stop(self) -> None:
        self._stop_event.set()
        self._close_sock()

    def reconfigure(self, **kwargs) -> bool:
        """热更新参数。仅当连接参数真的变了才拆线重连，避免前端每次保存都踢 NTRIP。"""
        changed = False
        if "host" in kwargs and kwargs["host"]:
            host = str(kwargs["host"])
            if host != self._host:
                self._host = host
                changed = True
        if "port" in kwargs and kwargs["port"]:
            port = int(kwargs["port"])
            if port != self._port:
                self._port = port
                changed = True
        if "mountpoint" in kwargs:
            mp = str(kwargs["mountpoint"] or "").lstrip("/")
            if mp != self._mountpoint:
                self._mountpoint = mp
                changed = True
        if "username" in kwargs:
            user = str(kwargs["username"] or "")
            if user != self._username:
                self._username = user
                changed = True
        if "password" in kwargs:
            pw = str(kwargs["password"] or "")
            if pw != self._password:
                self._password = pw
                changed = True
        if "gga_interval_s" in kwargs and kwargs["gga_interval_s"] is not None:
            interval = max(0.0, float(kwargs["gga_interval_s"]))
            if interval != self._gga_interval_s:
                self._gga_interval_s = interval
                changed = True
        if "reconnect_interval_s" in kwargs and kwargs["reconnect_interval_s"] is not None:
            self._reconnect_interval_s = max(1.0, float(kwargs["reconnect_interval_s"]))
        if changed:
            self._close_sock()
        return changed

    def _close_sock(self) -> None:
        self._connected = False
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _refresh_gga_from_provider(self) -> None:
        if self._gga_provider is None:
            return
        try:
            gga = self._gga_provider()
        except Exception as e:
            logger.warning("NTRIP 获取 GGA 失败: %s", e)
            return
        if gga:
            self.update_gga(gga)

    def run(self) -> None:
        logger.info(
            "NTRIP 客户端启动 → %s:%s/%s",
            self._host,
            self._port,
            self._mountpoint or "(empty)",
        )
        while not self._stop_event.is_set():
            if not self._mountpoint or not self._host:
                self._stop_event.wait(self._reconnect_interval_s)
                continue
            try:
                self._session()
            except Exception as e:
                logger.warning("NTRIP 会话异常: %s", e)
            self._close_sock()
            # 该挂载点会周期性拆线；ICOM 约 5s 无差分就 RST，必须马上重连
            if self._stop_event.wait(1.5):
                break
        logger.info("NTRIP 客户端已停止")

    def _session(self) -> None:
        sock = socket.create_connection((self._host, self._port), timeout=10.0)
        self._sock = sock
        sock.settimeout(1.0)

        auth = base64.b64encode(
            f"{self._username}:{self._password}".encode("ascii")
        ).decode("ascii")
        # User-Agent 需含 NTRIP，部分国内 caster 否则握手后不推流
        req = (
            f"GET /{self._mountpoint} HTTP/1.0\r\n"
            f"User-Agent: NTRIP RTG-Controller/1.0\r\n"
            f"Accept: */*\r\n"
            f"Authorization: Basic {auth}\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode("ascii"))

        # 读响应头
        header = b""
        while b"\r\n\r\n" not in header and len(header) < 4096:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError("NTRIP 连接在响应前关闭")
            header += chunk
        header_text = header.decode("ascii", errors="ignore")
        first = header_text.split("\r\n", 1)[0]
        if "200" not in first:
            raise ConnectionError(f"NTRIP 握手失败: {first}")

        self._connected = True
        self._rtcm_bytes = 0
        self._last_rtcm_log = time.time()
        logger.info("✓ NTRIP 已连接: %s:%s/%s", self._host, self._port, self._mountpoint)

        # 握手后立刻上报 GGA（VRS 必需），否则约 60s 会被踢且无差分流
        self._refresh_gga_from_provider()
        self._last_gga_sent = 0.0
        self._maybe_send_gga(sock, force=True)

        # 响应体可能粘在 header 后
        body = header.split(b"\r\n\r\n", 1)
        pending = body[1] if len(body) > 1 else b""
        if pending and self._on_rtcm:
            self._on_rtcm(pending)
            self._rtcm_bytes += len(pending)

        idle_since = time.time()
        while not self._stop_event.is_set():
            self._refresh_gga_from_provider()
            self._maybe_send_gga(sock)
            try:
                data = sock.recv(4096)
            except socket.timeout:
                # 长时间无字节：多为未发 GGA 或账号无流
                if time.time() - idle_since > 45.0 and self._rtcm_bytes == 0:
                    raise ConnectionError(
                        "NTRIP 已连接但 45s 无差分数据（请确认已有定位 GGA 上报、挂载点/账号有效）"
                    )
                continue
            if not data:
                raise ConnectionError("NTRIP 对端关闭")
            idle_since = time.time()
            self._rtcm_bytes += len(data)
            now = time.time()
            if now - self._last_rtcm_log >= 30.0:
                logger.info(
                    "NTRIP 差分流正常：累计 %.1f KB",
                    self._rtcm_bytes / 1024.0,
                )
                self._last_rtcm_log = now
            if self._on_rtcm:
                self._on_rtcm(data)

    def _maybe_send_gga(self, sock: socket.socket, force: bool = False) -> None:
        if self._gga_interval_s <= 0 and not force:
            return
        now = time.time()
        with self._gga_lock:
            gga = self._latest_gga
        if not gga:
            if force or (now - self._last_no_gga_warn) >= 10.0:
                logger.warning(
                    "NTRIP 已连接但尚无有效 GGA 可上报（等电气侧定位后自动补发）"
                )
                self._last_no_gga_warn = now
            return
        # 首次拿到有效 GGA 立即发（_last_gga_sent==0）；之后按间隔
        if (
            not force
            and self._last_gga_sent > 0.0
            and now - self._last_gga_sent < self._gga_interval_s
        ):
            return
        try:
            sock.sendall((gga + "\r\n").encode("ascii", errors="ignore"))
            self._last_gga_sent = now
            preview = gga if len(gga) <= 72 else gga[:72] + "..."
            if force or self._rtcm_bytes == 0:
                logger.info("NTRIP 已上报 GGA: %s", preview)
            else:
                logger.debug("NTRIP 已上报 GGA: %s", preview)
        except OSError as e:
            raise ConnectionError(f"NTRIP 发送 GGA 失败: {e}") from e
