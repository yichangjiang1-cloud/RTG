"""
通信模块：PLC TCP 客户端
负责与 PLC 建立持久 TCP 连接，周期性发送定位帧，
同时接收 PLC 下行帧；断连后自动重连，不阻塞主解算流程。
"""

import socket
import threading
import logging
import time
import queue
from typing import Callable, Optional

from src.bridge.plc_protocol_v1 import PlcToGnssFrame, try_extract_plc_frame

logger = logging.getLogger(__name__)

OnPlcFrameCallback = Callable[[PlcToGnssFrame], None]


class PlcClient(threading.Thread):
    """
    PLC TCP 客户端（独立线程）。

    - 启动后持续尝试连接 PLC
    - 外部通过 send(frame) 将帧放入发送队列
    - 连接期间轮询 recv，解析 PLC→GNSS 帧并回调
    - 发送失败时自动标记断连并重连
    - 队列满时丢弃最旧帧（保持实时性，避免积压）
    """

    QUEUE_MAX = 20

    def __init__(
        self,
        host: str,
        port: int,
        reconnect_interval: float = 3.0,
        on_plc_frame: Optional[OnPlcFrameCallback] = None,
    ):
        super().__init__(name="PlcClient", daemon=True)
        self._host = host
        self._port = port
        self._reconnect_interval = reconnect_interval
        self._on_plc_frame = on_plc_frame

        self._send_queue: queue.Queue[bytes] = queue.Queue(maxsize=self.QUEUE_MAX)
        self._stop_event = threading.Event()
        self._connected = False
        self._sock: Optional[socket.socket] = None
        self._recv_buffer = bytearray()

        self._sent_count = 0
        self._recv_count = 0
        self._dropped_count = 0
        self._error_count = 0
        self._last_error_log_time = 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict:
        return {
            "sent": self._sent_count,
            "recv": self._recv_count,
            "dropped": self._dropped_count,
            "errors": self._error_count,
        }

    def send(self, frame: bytes) -> bool:
        try:
            self._send_queue.put_nowait(frame)
            return True
        except queue.Full:
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            self._send_queue.put_nowait(frame)
            self._dropped_count += 1
            logger.debug("发送队列满，丢弃旧帧（总丢弃 %d）", self._dropped_count)
            return False

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        logger.info("PlcClient 线程启动 → %s:%d", self._host, self._port)
        while not self._stop_event.is_set():
            if not self._connected:
                self._try_connect()
                if not self._connected:
                    self._stop_event.wait(self._reconnect_interval)
                    continue

            self._io_loop()

        self._close_socket()
        logger.info("PlcClient 线程已停止")

    def _try_connect(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self._host, self._port))
            sock.settimeout(0.05)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = sock
            self._recv_buffer.clear()
            self._connected = True
            logger.info("已连接 PLC: %s:%d", self._host, self._port)
        except (OSError, socket.timeout) as e:
            logger.warning("连接 PLC 失败: %s，%g s 后重试", e, self._reconnect_interval)
            self._connected = False

    def _io_loop(self) -> None:
        """收发复用：优先读 PLC 下行，再发上行队列。"""
        while not self._stop_event.is_set() and self._connected:
            if not self._poll_recv():
                break

            try:
                frame = self._send_queue.get_nowait()
            except queue.Empty:
                continue

            if not self._send_frame(frame):
                break

    def _poll_recv(self) -> bool:
        """读取并解析 PLC 下行；返回 False 表示连接已断开。"""
        if not self._sock:
            return False
        try:
            chunk = self._sock.recv(4096)
        except socket.timeout:
            return True
        except OSError as e:
            self._mark_disconnected(f"PLC 接收失败: {e}")
            return False

        if not chunk:
            self._mark_disconnected("PLC 连接被对端关闭")
            return False

        self._recv_buffer.extend(chunk)
        while True:
            plc_frame, self._recv_buffer = try_extract_plc_frame(self._recv_buffer)
            if plc_frame is None:
                break
            self._recv_count += 1
            if self._on_plc_frame:
                try:
                    self._on_plc_frame(plc_frame)
                except Exception:
                    logger.exception("PLC 下行帧回调异常")
        return True

    def _send_frame(self, frame: bytes) -> bool:
        if not self._sock:
            return False
        try:
            self._sock.sendall(frame)
            self._sent_count += 1
            return True
        except OSError as e:
            self._mark_disconnected(f"PLC 发送失败: {e}")
            return False

    def _mark_disconnected(self, reason: str) -> None:
        self._error_count += 1
        now = time.time()
        if now - self._last_error_log_time >= 30.0:
            logger.warning("%s（%g s 内不再重复告警）", reason, 30.0)
            self._last_error_log_time = now
        self._connected = False
        self._close_socket()

    def _close_socket(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False
