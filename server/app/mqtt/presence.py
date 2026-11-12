"""
中控在线状态：靠 MQTT 流量判定，不写库。

中控每 5s 发 status/heartbeat，遥测 10Hz。
超过 ONLINE_TIMEOUT_S 无任何 MQTT 则视为离线。
收到 event=offline 立即下线。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Optional

ONLINE_TIMEOUT_S = 15.0


class VehiclePresence:
    def __init__(self, timeout_s: float = ONLINE_TIMEOUT_S):
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._timeout_s = float(timeout_s)

    def touch(self, vehicle_id: str) -> None:
        vid = (vehicle_id or "").strip()
        if not vid:
            return
        with self._lock:
            self._last[vid] = time.time()

    def mark_offline(self, vehicle_id: str) -> None:
        vid = (vehicle_id or "").strip()
        if not vid:
            return
        with self._lock:
            self._last.pop(vid, None)

    def is_online(self, vehicle_id: str) -> bool:
        t = self.last_seen_ts(vehicle_id)
        if t is None:
            return False
        return (time.time() - t) <= self._timeout_s

    def last_seen_ts(self, vehicle_id: str) -> Optional[float]:
        vid = (vehicle_id or "").strip()
        if not vid:
            return None
        with self._lock:
            return self._last.get(vid)

    def last_seen_iso(self, vehicle_id: str) -> Optional[str]:
        t = self.last_seen_ts(vehicle_id)
        if t is None:
            return None
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()

    def online_ids(self) -> list[str]:
        now = time.time()
        with self._lock:
            return [vid for vid, t in self._last.items() if now - t <= self._timeout_s]


presence = VehiclePresence()
