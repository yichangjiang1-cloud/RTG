"""
GNSS 接收机 UDP 原始数据落盘。

目录结构（默认 logs/gnss_raw/）：
  receiver_1_gantry_a1/   ← 左列 / 9001
  receiver_2_gantry_a2/   ← 右列 / 9002
  receiver_3_trolley/     ← 小车 / 9003

每次「连接会话」一个文件：首包、断流超过 session_gap_s 后重连、或发送端 IP:端口变化时新建。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional, Tuple

from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

# role → 子目录（接收机 1 / 2 / 3）
_RECEIVER_SUBDIRS = {
    "gantry_a1": "receiver_1_gantry_a1",
    "gantry_a2": "receiver_2_gantry_a2",
    "trolley": "receiver_3_trolley",
}


class GnssRawFileLogger:
    """线程安全的单路 GNSS 原始 UDP 落盘。"""

    def __init__(
        self,
        receiver_id: str,
        listen_port: int,
        base_dir: str | Path,
        session_gap_s: float = 2.0,
    ):
        self.receiver_id = receiver_id
        self.listen_port = listen_port
        self.base_dir = Path(base_dir)
        sub = _RECEIVER_SUBDIRS.get(receiver_id, receiver_id)
        self._dir = self.base_dir / sub
        self._session_gap_s = max(session_gap_s, 0.5)
        self._lock = threading.Lock()
        self._file: Optional[BinaryIO] = None
        self._path: Optional[Path] = None
        self._last_write_time: float = 0.0
        self._peer: Optional[Tuple[str, int]] = None
        self._dir.mkdir(parents=True, exist_ok=True)

    def _session_stale(self, now: float, peer: Tuple[str, int]) -> bool:
        if self._file is None:
            return True
        if self._peer != peer:
            return True
        if self._last_write_time > 0 and (now - self._last_write_time) > self._session_gap_s:
            return True
        return False

    def _open_session(self, peer: Tuple[str, int], now: float) -> None:
        self._close_session()
        host, src_port = peer
        ts = datetime.fromtimestamp(now).strftime("%Y%m%d_%H%M%S")
        safe_host = host.replace(":", "_")
        name = f"{ts}_{safe_host}_{src_port}_port{self.listen_port}.nmea"
        path = self._dir / name
        fh = path.open("ab")
        header = (
            f"# GNSS raw session receiver={self.receiver_id} "
            f"listen_port={self.listen_port} peer={host}:{src_port} "
            f"started={datetime.fromtimestamp(now).isoformat(timespec='seconds')}\n"
        ).encode("ascii")
        fh.write(header)
        self._file = fh
        self._path = path
        self._peer = peer
        self._last_write_time = now
        logger.info(
            f"[{self.receiver_id}] 原始数据记录 → {path}"
        )

    def write_packet(self, data: bytes, addr: tuple, now: float) -> None:
        peer = (str(addr[0]), int(addr[1]))
        with self._lock:
            if self._session_stale(now, peer):
                self._open_session(peer, now)
            assert self._file is not None
            self._file.write(data)
            if not data.endswith(b"\n"):
                self._file.write(b"\n")
            self._file.flush()
            self._last_write_time = now

    def end_session_if_idle(self, now: float) -> None:
        """断流超过阈值时关闭当前文件，下次写入会新建。"""
        with self._lock:
            if self._file is None or self._last_write_time <= 0:
                return
            if (now - self._last_write_time) <= self._session_gap_s:
                return
            self._close_session()

    def close(self) -> None:
        with self._lock:
            self._close_session()

    def _close_session(self) -> None:
        if self._file is None:
            return
        try:
            self._file.flush()
            self._file.close()
        finally:
            if self._path is not None:
                logger.info(
                    f"[{self.receiver_id}] 原始数据会话结束 → {self._path.name}"
                )
            self._file = None
            self._path = None
            self._peer = None


def create_gnss_raw_file_logger(
    receiver_id: str,
    listen_port: int,
) -> Optional[GnssRawFileLogger]:
    """按 system.yaml → gnss.raw_log 创建落盘器；未启用时返回 None。"""
    try:
        from src.utils.config_loader import get_config

        cfg = get_config()
        if not cfg.get("gnss.raw_log.enabled", True):
            return None
        base = cfg.get("gnss.raw_log.dir", "logs/gnss_raw")
        gap = float(cfg.get("gnss.raw_log.session_gap_s", 2.0))
    except Exception:
        base = "logs/gnss_raw"
        gap = 2.0
    return GnssRawFileLogger(receiver_id, listen_port, base, gap)
