#!/usr/bin/env python3
"""
TCP 客户端：连接 GNSS 接收机，打印 NMEA，并可把 NTRIP 差分灌进同一条 TCP。

默认对接 system.yaml 里 gantry_a1：192.168.8.172:30001。
接收机通常只允许一路 TCP，先停掉 run_controller 再跑本脚本。
接收机 TCP 断开后直接退出，不重连。

差分默认读 config/vehicle_params.yaml 的 ntrip 段。
NTRIP 线程只入队，接收机 socket 只由收 NMEA 的循环点写（避免半双工 RST）。

用法：
  python tools/gnss_tcp_client.py
  python tools/gnss_tcp_client.py --no-ntrip
  python tools/gnss_tcp_client.py --host 192.168.8.172 --port 30001
"""

from __future__ import annotations

import argparse
import base64
import select
import socket
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.driver.rtcm_inject import (
    RTCM_BPS,
    RtcmOutQueue,
    RtcmPacer,
    flush_rtcm_drain,
)

DEFAULT_HOST = "192.168.8.172"
DEFAULT_PORT = 30001
DEFAULT_YAML = ROOT / "config" / "vehicle_params.yaml"

_NTRIP_FALLBACK = {
    "caster_host": "103.143.19.54",
    "caster_port": 8002,
    "mountpoint": "RTCM33GRCEJpro",
    "username": "YCJ004",
    "password": "47293",
    "gga_interval_s": 10.0,
}


def load_ntrip_from_yaml(path: Path) -> dict:
    cfg = dict(_NTRIP_FALLBACK)
    try:
        import yaml
    except ImportError:
        print(f"未安装 PyYAML，使用内置 NTRIP 默认值 → {cfg['caster_host']}:{cfg['caster_port']}", flush=True)
        return cfg
    if not path.is_file():
        print(f"未找到 {path}，使用内置 NTRIP 默认值", flush=True)
        return cfg
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError as e:
        print(f"读取 {path} 失败: {e}，使用内置 NTRIP 默认值", flush=True)
        return cfg
    ntrip = data.get("ntrip") or {}
    for key in _NTRIP_FALLBACK:
        val = ntrip.get(key)
        if val not in (None, ""):
            cfg[key] = val
    cfg["caster_port"] = int(cfg["caster_port"])
    cfg["gga_interval_s"] = float(cfg.get("gga_interval_s") or 10.0)
    print(
        f"NTRIP 配置来自 {path.name}: {cfg['caster_host']}:{cfg['caster_port']}/{cfg['mountpoint']}",
        flush=True,
    )
    return cfg


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GNSS 接收机 TCP 客户端（拉 NMEA + 可选灌差分）")
    p.add_argument("--host", default=DEFAULT_HOST, help="接收机 IP")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="接收机端口")
    p.add_argument("--timeout", type=float, default=5.0, help="连接超时（秒）")
    p.add_argument("--send", default="", help="连上后发送一行命令（自动补 \\r\\n）")
    p.add_argument("--ntrip-yaml", default=str(DEFAULT_YAML), help="读取 NTRIP 的 yaml")
    p.add_argument("--no-ntrip", action="store_true", help="只收 NMEA，不连 caster、不灌差分")
    p.add_argument("--ntrip-host", default="", help="覆盖 caster_host")
    p.add_argument("--ntrip-port", type=int, default=0, help="覆盖 caster_port")
    p.add_argument("--ntrip-mount", default="", help="覆盖 mountpoint")
    p.add_argument("--ntrip-user", default="", help="覆盖 username")
    p.add_argument("--ntrip-password", default="", help="覆盖 password")
    p.add_argument("--gga-interval", type=float, default=0.0, help="向 caster 上报 GGA 间隔（秒，0=用 yaml）")
    p.add_argument(
        "--rtcm-bps",
        type=int,
        default=RTCM_BPS,
        help=f"灌入接收机的 RTCM 上限（字节/秒，默认 {RTCM_BPS}）",
    )
    return p.parse_args()


def _print_line(text: str) -> None:
    line = text.strip()
    if line:
        print(line, flush=True)


def _is_gga(line: str) -> bool:
    return line.startswith("$") and len(line) >= 7 and line[3:6].upper() == "GGA"


def _gga_for_ntrip(raw: str) -> str:
    """部分国内 caster 只认 $GPGGA。"""
    line = (raw or "").strip()
    if not line.startswith("$") or "*" not in line:
        return line
    body, _, _ = line[1:].partition("*")
    if body.upper().startswith("GN") and "GGA" in body.upper()[:6]:
        body = "GP" + body[2:]
    csum = 0
    for ch in body:
        csum ^= ord(ch)
    return f"${body}*{csum:02X}"


class NtripClientThread(threading.Thread):
    """只连 caster、把 RTCM 放进队列，不碰接收机 socket。"""

    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        username: str,
        password: str,
        gga_interval_s: float,
        latest_gga: list,
        rtcm_q: RtcmOutQueue,
        stop_event: threading.Event,
    ):
        super().__init__(name="ntrip-client", daemon=True)
        self._host = host
        self._port = int(port)
        self._mountpoint = (mountpoint or "").lstrip("/")
        self._username = username or ""
        self._password = password or ""
        self._gga_interval_s = max(0.0, float(gga_interval_s))
        self._latest_gga = latest_gga
        self._rtcm_q = rtcm_q
        self._stop_event = stop_event
        self._rtcm_bytes = 0
        self._last_log = 0.0
        self._last_gga_sent = 0.0

    def run(self) -> None:
        print(
            f"NTRIP 启动 → {self._host}:{self._port}/{self._mountpoint}（只入队，由收包循环点写）",
            flush=True,
        )
        while not self._stop_event.is_set():
            try:
                self._session()
            except Exception as e:
                print(f"NTRIP 会话异常: {e}", flush=True)
            print("NTRIP 1.5s 后重连（避免接收机约 5s 无差分被 RST）", flush=True)
            if self._stop_event.wait(1.5):
                break
        print("NTRIP 已停止", flush=True)

    def _enqueue(self, data: bytes) -> None:
        if not data:
            return
        self._rtcm_q.put(data)
        self._rtcm_bytes += len(data)
        now = time.time()
        if self._rtcm_bytes == len(data):
            print(f"首包 RTCM {len(data)} 字节已入队", flush=True)
        if now - self._last_log >= 10.0:
            print(
                f"RTCM 入队累计 {self._rtcm_bytes / 1024.0:.1f} KB，"
                f"待写 {self._rtcm_q.pending()} B，丢弃 {self._rtcm_q.dropped} B",
                flush=True,
            )
            self._last_log = now

    def _session(self) -> None:
        sock = socket.create_connection((self._host, self._port), timeout=10.0)
        sock.settimeout(1.0)
        try:
            auth = base64.b64encode(
                f"{self._username}:{self._password}".encode("ascii")
            ).decode("ascii")
            req = (
                f"GET /{self._mountpoint} HTTP/1.0\r\n"
                f"User-Agent: NTRIP RTG-Controller/1.0\r\n"
                f"Accept: */*\r\n"
                f"Authorization: Basic {auth}\r\n"
                f"\r\n"
            )
            sock.sendall(req.encode("ascii"))

            header = b""
            while b"\r\n\r\n" not in header and len(header) < 4096:
                chunk = sock.recv(256)
                if not chunk:
                    raise ConnectionError("NTRIP 连接在响应前关闭")
                header += chunk
            first = header.decode("ascii", errors="ignore").split("\r\n", 1)[0]
            if "200" not in first:
                raise ConnectionError(f"NTRIP 握手失败: {first}")

            print(f"✓ NTRIP 已连接: {self._host}:{self._port}/{self._mountpoint}", flush=True)
            self._last_gga_sent = 0.0
            self._maybe_send_gga(sock, force=True)

            body = header.split(b"\r\n\r\n", 1)
            if len(body) > 1 and body[1]:
                self._enqueue(body[1])

            idle_since = time.time()
            session_rtcm = 0
            while not self._stop_event.is_set():
                self._maybe_send_gga(sock)
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    if time.time() - idle_since > 45.0 and session_rtcm == 0:
                        raise ConnectionError(
                            "NTRIP 已连接但 45s 无差分（等接收机 GGA，或检查挂载点/账号）"
                        )
                    continue
                if not data:
                    raise ConnectionError("NTRIP 对端关闭")
                idle_since = time.time()
                session_rtcm += len(data)
                self._enqueue(data)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _maybe_send_gga(self, sock: socket.socket, force: bool = False) -> None:
        if self._gga_interval_s <= 0 and not force:
            return
        now = time.time()
        gga = self._latest_gga[0]
        if not gga:
            return
        if (
            not force
            and self._last_gga_sent > 0.0
            and now - self._last_gga_sent < self._gga_interval_s
        ):
            return
        line = _gga_for_ntrip(gga)
        sock.sendall((line + "\r\n").encode("ascii", errors="ignore"))
        self._last_gga_sent = now
        preview = line if len(line) <= 72 else line[:72] + "..."
        print(f"NTRIP 已上报 GGA: {preview}", flush=True)


def _feed_nmea(buf: str, chunk: bytes, latest_gga: list, print_q: Queue) -> str:
    buf += chunk.decode("ascii", errors="replace")
    while True:
        idx_n = buf.find("\n")
        idx_r = buf.find("\r")
        seps = [i for i in (idx_n, idx_r) if i >= 0]
        if not seps:
            break
        cut = min(seps)
        line = buf[:cut].strip()
        buf = buf[cut + 1 :]
        if buf.startswith("\n") or buf.startswith("\r"):
            buf = buf[1:]
        if _is_gga(line):
            latest_gga[0] = line
        if line:
            try:
                print_q.put_nowait(line)
            except Full:
                pass
    if len(buf) > 8192:
        buf = buf[-2048:]
    return buf


def evk_loop(
    sock: socket.socket,
    latest_gga: list,
    print_q: Queue,
    rtcm_q: RtcmOutQueue,
    pacer: RtcmPacer,
    stop_event: threading.Event,
) -> None:
    """本线程独占接收机 socket：先排空 NMEA，再在可写时点写 RTCM。"""
    sock.setblocking(False)
    buf = ""
    while not stop_event.is_set():
        want_w = rtcm_q.pending() > 0 and pacer.allow() > 0
        try:
            readable, writable, _ = select.select(
                [sock], [sock] if want_w else [], [], 0.2
            )
        except OSError as e:
            print_q.put(f"接收机 TCP 异常: {e}")
            return

        if sock in readable:
            try:
                chunk = sock.recv(4096)
            except BlockingIOError:
                chunk = None
            except OSError as e:
                print_q.put(f"接收机 TCP 异常: {e}")
                return
            if chunk == b"":
                print_q.put("对端关闭连接")
                return
            if chunk:
                buf = _feed_nmea(buf, chunk, latest_gga, print_q)
                try:
                    flush_rtcm_drain(sock, rtcm_q, pacer)
                except OSError as e:
                    print_q.put(f"接收机 TCP 异常: {e}")
                    return
        elif want_w and sock in writable:
            try:
                flush_rtcm_drain(sock, rtcm_q, pacer, rounds=2)
            except OSError as e:
                print_q.put(f"接收机 TCP 异常: {e}")
                return


def _printer_loop(print_q: Queue, stop_event: threading.Event) -> None:
    while not stop_event.is_set() or not print_q.empty():
        try:
            line = print_q.get(timeout=0.2)
        except Empty:
            continue
        _print_line(line)


def main() -> int:
    args = parse_args()
    ntrip_cfg = load_ntrip_from_yaml(Path(args.ntrip_yaml))
    if args.ntrip_host:
        ntrip_cfg["caster_host"] = args.ntrip_host
    if args.ntrip_port:
        ntrip_cfg["caster_port"] = args.ntrip_port
    if args.ntrip_mount:
        ntrip_cfg["mountpoint"] = args.ntrip_mount
    if args.ntrip_user:
        ntrip_cfg["username"] = args.ntrip_user
    if args.ntrip_password:
        ntrip_cfg["password"] = args.ntrip_password
    if args.gga_interval > 0:
        ntrip_cfg["gga_interval_s"] = args.gga_interval

    print(f"连接 {args.host}:{args.port} …", flush=True)
    try:
        sock = socket.create_connection((args.host, args.port), timeout=args.timeout)
    except OSError as e:
        print(f"连接失败: {e}", flush=True)
        return 1
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
    except OSError:
        pass
    print(f"已连接 {args.host}:{args.port}", flush=True)

    latest_gga: list[Optional[str]] = [None]
    stop_event = threading.Event()
    print_q: Queue = Queue(maxsize=500)
    rtcm_q = RtcmOutQueue()
    pacer = RtcmPacer(bps=int(args.rtcm_bps))
    ntrip_thread: Optional[NtripClientThread] = None
    printer = threading.Thread(
        target=_printer_loop, args=(print_q, stop_event), name="nmea-print", daemon=True
    )
    printer.start()

    if args.send:
        payload = args.send if args.send.endswith(("\r\n", "\n")) else args.send + "\r\n"
        sock.sendall(payload.encode("ascii", errors="replace"))
        print(f">> {args.send}", flush=True)

    if not args.no_ntrip:
        ntrip_thread = NtripClientThread(
            host=str(ntrip_cfg["caster_host"]),
            port=int(ntrip_cfg["caster_port"]),
            mountpoint=str(ntrip_cfg["mountpoint"]),
            username=str(ntrip_cfg["username"]),
            password=str(ntrip_cfg["password"]),
            gga_interval_s=float(ntrip_cfg["gga_interval_s"]),
            latest_gga=latest_gga,
            rtcm_q=rtcm_q,
            stop_event=stop_event,
        )
        ntrip_thread.start()
    else:
        print("已关闭 NTRIP（--no-ntrip），仅收 NMEA", flush=True)

    try:
        evk_loop(sock, latest_gga, print_q, rtcm_q, pacer, stop_event)
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
    finally:
        stop_event.set()
        try:
            sock.close()
        except OSError:
            pass
        if ntrip_thread is not None:
            ntrip_thread.join(timeout=2.0)
        printer.join(timeout=1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
