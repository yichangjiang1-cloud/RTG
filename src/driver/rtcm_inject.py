"""
接收机 ICOM 同口灌差分：只入队，由收 NMEA 的那条线程在 socket 可写时点写。

另一线程 sendall 会和 recv 抢同一条 TCP；板卡发送窗一堵，NMEA 吐不出、差分龄爬升，随后 RST。
"""

from __future__ import annotations

import select
import threading
import time

# 单次最多写出；优先把读路径让出来
RTCM_CHUNK = 256
# 上限，避免瞬时灌满 ICOM 接收窗
RTCM_BPS = 8000
# 积压超过此值丢最旧字节，保住最新 MSM
RTCM_QUEUE_MAX = 8192


class RtcmOutQueue:
    def __init__(self, max_bytes: int = RTCM_QUEUE_MAX):
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._max = max(512, int(max_bytes))
        self.dropped = 0
        self.enqueued = 0

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def put(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._buf.extend(data)
            extra = len(self._buf) - self._max
            if extra > 0:
                del self._buf[:extra]
                self.dropped += extra
            self.enqueued += len(data)

    def pending(self) -> int:
        with self._lock:
            return len(self._buf)

    def prepend(self, data: bytes) -> None:
        """发送失败的字节必须回到队头，不能 put 到队尾（会打乱 RTCM）。"""
        if not data:
            return
        with self._lock:
            self._buf[0:0] = data
            extra = len(self._buf) - self._max
            if extra > 0:
                del self._buf[-extra:]
                self.dropped += extra

    def take(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            return chunk


class RtcmPacer:
    def __init__(self, bps: int = RTCM_BPS, chunk: int = RTCM_CHUNK):
        self.bps = max(500, int(bps))
        self.chunk = max(32, int(chunk))
        self._tokens = float(self.chunk)
        self._last = time.monotonic()

    def allow(self) -> int:
        now = time.monotonic()
        self._tokens = min(float(self.bps), self._tokens + (now - self._last) * self.bps)
        self._last = now
        return min(self.chunk, int(self._tokens))

    def consume(self, n: int) -> None:
        self._tokens = max(0.0, self._tokens - n)


def flush_rtcm_writable(
    sock, queue: RtcmOutQueue, pacer: RtcmPacer, *, require_select: bool = False
) -> int:
    """写出一小段。刚读完 NMEA 后不要先 select（Windows 上常误报不可写）。"""
    n = min(pacer.allow(), queue.pending())
    if n <= 0:
        return 0
    if require_select:
        try:
            _, w, _ = select.select([], [sock], [], 0)
        except OSError:
            return 0
        if sock not in w:
            return 0
    payload = queue.take(n)
    if not payload:
        return 0
    try:
        sent = sock.send(payload)
    except BlockingIOError:
        queue.prepend(payload)
        return 0
    except OSError:
        queue.prepend(payload)
        raise
    if sent < len(payload):
        queue.prepend(payload[sent:])
    if sent:
        pacer.consume(sent)
    return sent


def flush_rtcm_drain(sock, queue: RtcmOutQueue, pacer: RtcmPacer, rounds: int = 4) -> int:
    """读完 NMEA 后连续点写几段，仍受 pacer / 可写窗口限制。"""
    total = 0
    for _ in range(max(1, rounds)):
        n = flush_rtcm_writable(sock, queue, pacer)
        if n <= 0:
            break
        total += n
    return total
