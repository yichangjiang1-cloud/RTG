"""
通信模块：PLC TCP 客户端
负责与 PLC 建立持久 TCP 连接，周期性发送定位帧，
断连后自动重连，不阻塞主解算流程。
"""

import socket
import threading
import logging
import time
import queue
from typing import Optional

logger = logging.getLogger(__name__)


class PlcClient(threading.Thread):
    """
    PLC TCP 客户端（独立线程）。

    - 启动后持续尝试连接 PLC
    - 外部通过 send(frame) 将帧放入发送队列
    - 发送失败时自动标记断连并重连
    - 队列满时丢弃最旧帧（保持实时性，避免积压）
    """

    # 发送队列最大深度（超出时丢弃旧帧）
    QUEUE_MAX = 20

    def __init__(self, host: str, port: int, reconnect_interval: float = 3.0):
        """
        :param host:               PLC IP 地址
        :param port:               PLC 端口
        :param reconnect_interval: 断连后重连等待间隔（秒）
        """
        super().__init__(name="PlcClient", daemon=True)
        self._host              = host
        self._port              = port
        self._reconnect_interval = reconnect_interval

        self._send_queue: queue.Queue[bytes] = queue.Queue(maxsize=self.QUEUE_MAX)
        self._stop_event  = threading.Event()
        self._connected   = False
        self._sock: Optional[socket.socket] = None

        # 统计
        self._sent_count    = 0
        self._dropped_count = 0
        self._error_count   = 0
        self._last_error_log_time = 0.0

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> dict:
        """返回发送统计信息。"""
        return {
            "sent":    self._sent_count,
            "dropped": self._dropped_count,
            "errors":  self._error_count,
        }

    def send(self, frame: bytes) -> bool:
        """
        将帧放入发送队列（非阻塞）。

        :return: True=入队成功, False=队列满被丢弃
        """
        try:
            self._send_queue.put_nowait(frame)
            return True
        except queue.Full:
            # 队列满：丢弃最旧帧，保留最新帧
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            self._send_queue.put_nowait(frame)
            self._dropped_count += 1
            logger.debug("发送队列满，丢弃旧帧（总丢弃 %d）", self._dropped_count)
            return False

    def stop(self) -> None:
        """通知线程停止。"""
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 线程主循环
    # ------------------------------------------------------------------
    def run(self) -> None:
        logger.info("PlcClient 线程启动 → %s:%d", self._host, self._port)
        while not self._stop_event.is_set():
            if not self._connected:
                self._try_connect()
                if not self._connected:
                    self._stop_event.wait(self._reconnect_interval)
                    continue

            self._send_loop()

        self._close_socket()
        logger.info("PlcClient 线程已停止")

    def _try_connect(self) -> None:
        """尝试建立 TCP 连接。"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self._host, self._port))
            sock.settimeout(None)   # 切换为阻塞模式
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock      = sock
            self._connected = True
            logger.info("已连接 PLC: %s:%d", self._host, self._port)
        except (OSError, socket.timeout) as e:
            logger.warning("连接 PLC 失败: %s，%g s 后重试", e, self._reconnect_interval)
            self._connected = False

    def _send_loop(self) -> None:
        """持续从队列取帧并发送，直到连接断开或收到停止信号。"""
        while not self._stop_event.is_set() and self._connected:
            try:
                frame = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                # 发送心跳（可选：保持连接）
                continue

            try:
                self._sock.sendall(frame)
                self._sent_count += 1
            except OSError as e:
                self._error_count += 1
                now = time.time()
                if now - self._last_error_log_time >= 30.0:
                    logger.warning(
                        "PLC 发送失败，连接断开: %s（%g s 内不再重复告警）",
                        e, 30.0,
                    )
                    self._last_error_log_time = now
                self._connected = False
                self._close_socket()
                break

    def _close_socket(self) -> None:
        """安全关闭 Socket。"""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock      = None
            self._connected = False

