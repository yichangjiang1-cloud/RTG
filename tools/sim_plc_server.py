#!/usr/bin/env python3
"""
模拟 PLC 端（TCP 服务端），与中控机联调。

协议约定（V1.0.0）：
  - PLC = TCP 服务端，中控 = TCP 客户端（中控主动连上来）
  - 小端，帧头 0xFEEF，帧尾 0x0A0D
  - GNSS→PLC：158 B（中控上行）
  - PLC→GNSS：68 B（本脚本下行，回显心跳）

用法：
  # 1) 先启动本脚本（默认 0.0.0.0:5000）
  python tools/sim_plc_server.py

  # 2) system.yaml 打开 PLC：
  #    plc.enabled: true
  #    plc.host: 127.0.0.1   # 本机联调
  #    plc.port: 5000
  # 3) 重启中控

  python tools/sim_plc_server.py --host 0.0.0.0 --port 5000
  python tools/sim_plc_server.py --reply-hz 10 --quiet
"""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bridge.plc_protocol_v1 import (
    FRAME_FOOTER,
    FRAME_MAGIC,
    GNSS_TO_PLC_SIZE,
    PLC_TO_GNSS_SIZE,
)

_HEADER_FMT = "<HHHH"
_GNSS_BODY_FMT = (
    "<HHHH"
    "8s"
    "HH"
    "hhhh"
    "II"
    "H"
    "hhh"
    "HH"
    + "IIH" * 6
    + "20H"
    + "H"
)
_PLC_BODY_FMT = "<8s10sIIhhhhhH10HH"


def _unpack_ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _pack_ascii(text: str, size: int) -> bytes:
    return (text or "").encode("ascii", errors="ignore")[:size].ljust(size, b"\x00")


def _u32_as_i32(v: int) -> int:
    """协议把有符号 mm 按 uint32 位模式发送，还原为有符号。"""
    return struct.unpack("<i", struct.pack("<I", int(v) & 0xFFFFFFFF))[0]


_ANTENNA_NAMES = [
    "电气侧GNSS1(a1)",
    "电气侧GNSS2(a2)",
    "动力侧GNSS1(b1)",
    "动力侧GNSS2(b2)",
    "小车GNSS1",
    "小车GNSS2",
]


def decode_gnss_to_plc(data: bytes) -> dict | None:
    """解析中控上行 158B 帧的全部字段。"""
    if len(data) < GNSS_TO_PLC_SIZE:
        return None
    chunk = data[:GNSS_TO_PLC_SIZE]
    magic, hb, peer, seq = struct.unpack_from(_HEADER_FMT, chunk, 0)
    if magic != FRAME_MAGIC:
        return None
    rest = struct.unpack_from(_GNSS_BODY_FMT, chunk, 8)
    if rest[-1] != FRAME_FOOTER:
        return None

    # 六天线 XYZ：rest[19..36]，每根 (X u32→i32, Y u32→i32, Z u16)
    antennas = []
    base = 19
    for i in range(6):
        x = _u32_as_i32(rest[base + i * 3 + 0])
        y = _u32_as_i32(rest[base + i * 3 + 1])
        z = rest[base + i * 3 + 2]
        antennas.append({"name": _ANTENNA_NAMES[i], "x_mm": x, "y_mm": y, "z_mm": z})

    reserved = list(rest[37:57])  # 字段 42：20 个预留 H

    return {
        "heartbeat": hb,
        "peer_heartbeat": peer,
        "message_seq": seq,
        "fix_left": rest[0],
        "fix_right": rest[1],
        "fix_trolley": rest[2],
        "rtg_mode": rest[3],
        "yard_code": _unpack_ascii(rest[4]),
        "bay_no": rest[5],
        "yard_in_out": rest[6],
        "leg_mm": list(rest[7:11]),
        "gantry_elec_dist_mm": _u32_as_i32(rest[11]),
        "gantry_diesel_dist_mm": _u32_as_i32(rest[12]),
        "trolley_travel_mm": rest[13],
        "speed_gantry_elec_mm_s": rest[14],
        "speed_gantry_diesel_mm_s": rest[15],
        "speed_trolley_mm_s": rest[16],
        "cross_yard_deviation": rest[17],
        "cross_yard_flag": rest[18],
        "antennas": antennas,
        "reserved": reserved,
    }


def try_extract_gnss_frame(buf: bytearray) -> tuple[dict | None, bytearray]:
    while len(buf) >= GNSS_TO_PLC_SIZE:
        if struct.unpack_from("<H", buf, 0)[0] != FRAME_MAGIC:
            del buf[0]
            continue
        parsed = decode_gnss_to_plc(bytes(buf[:GNSS_TO_PLC_SIZE]))
        del buf[:GNSS_TO_PLC_SIZE]
        if parsed is not None:
            return parsed, buf
    return None, buf


def encode_plc_to_gnss(
    *,
    heartbeat: int,
    peer_heartbeat: int,
    message_seq: int,
    yard_code: str = "A01",
    rtg_id: str = "RTG-001",
    gantry_pos_mm: int = 0,
    trolley_pos_mm: int = 0,
) -> bytes:
    """构造 PLC→GNSS 下行 68B（心跳回显 + 占位位置）。"""
    body = struct.pack(
        _PLC_BODY_FMT,
        _pack_ascii(yard_code, 8),
        _pack_ascii(rtg_id, 10),
        int(gantry_pos_mm) & 0xFFFFFFFF,
        int(trolley_pos_mm) & 0xFFFFFFFF,
        0,
        0,
        0,
        0,
        0,
        0,  # gantry_run_status
        *([0] * 10),
        FRAME_FOOTER,
    )
    header = struct.pack(
        _HEADER_FMT,
        FRAME_MAGIC,
        heartbeat & 0xFFFF,
        peer_heartbeat & 0xFFFF,
        message_seq & 0xFFFF,
    )
    frame = header + body
    assert len(frame) == PLC_TO_GNSS_SIZE
    return frame


def format_uplink(f: dict) -> str:
    return (
        f"seq={f['message_seq']:5d} hb={f['heartbeat']:5d} "
        f"yard={f['yard_code']!r:8s} bay={f['bay_no']:3d} "
        f"fix L/R/T={f['fix_left']}/{f['fix_right']}/{f['fix_trolley']} "
        f"dist16/17={f['gantry_elec_dist_mm']}/{f['gantry_diesel_dist_mm']} mm "
        f"trolley18={f['trolley_travel_mm']} mm "
        f"spd_g/t={f['speed_gantry_elec_mm_s']}/{f['speed_trolley_mm_s']} mm/s"
    )


def format_uplink_full(f: dict) -> str:
    """打印中控→PLC 的全部字段（含六天线、门腿、预留）。"""
    leg = f["leg_mm"]
    lines = [
        "==================== 中控 → PLC (158B) ====================",
        f"  帧头  魔数=0xFEEF  本端心跳={f['heartbeat']}  对端心跳={f['peer_heartbeat']}  信息编号={f['message_seq']}",
        f"  5  电气侧定位状态 = {f['fix_left']}  (主×10+副, 4=RTK固定 5=浮点)",
        f"  6  动力侧定位状态 = {f['fix_right']}",
        f"  7  小车定位状态   = {f['fix_trolley']}",
        f"  8  RTG 工作模式   = {f['rtg_mode']}",
        f"  9  场号           = {f['yard_code']!r}",
        f"  10 当前贝位号     = {f['bay_no']}",
        f"  11 在场标记       = {f['yard_in_out']}  (0X00在场 0X01过街场 0x02...)",
        f"  12 左前门腿偏差   = {leg[0]} mm",
        f"  13 左后门腿偏差   = {leg[1]} mm",
        f"  14 右前门腿偏差   = {leg[2]} mm",
        f"  15 右后门腿偏差   = {leg[3]} mm  (场内负/场外正)",
        f"  16 电气侧作业点距原点 = {f['gantry_elec_dist_mm']} mm  (√(X²+Y²))",
        f"  17 动力侧作业点距原点 = {f['gantry_diesel_dist_mm']} mm",
        f"  18 小车相对起始点行程 = {f['trolley_travel_mm']} mm",
        f"  19 电气侧大车速度 = {f['speed_gantry_elec_mm_s']} mm/s  (增大为正)",
        f"  20 动力侧大车速度 = {f['speed_gantry_diesel_mm_s']} mm/s",
        f"  21 小车速度       = {f['speed_trolley_mm_s']} mm/s",
        f"  22 过街场偏差标志 = {f['cross_yard_deviation']}",
        f"  23 过街场标志     = {f['cross_yard_flag']}",
        "  24-41 六天线 XYZ（相对基准站, mm）:",
    ]
    for a in f["antennas"]:
        lines.append(
            f"        {a['name']:16s} X={a['x_mm']:>10} Y={a['y_mm']:>10} Z={a['z_mm']:>8}"
        )
    nonzero = [(i, v) for i, v in enumerate(f["reserved"]) if v != 0]
    if nonzero:
        lines.append(f"  42 预留(非零) = {nonzero}")
    else:
        lines.append("  42 预留 = 全 0")
    lines.append("=========================================================")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="模拟 PLC（TCP 服务端）↔ 中控")
    p.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    p.add_argument("--port", type=int, default=5000, help="监听端口，须与 plc.port 一致")
    p.add_argument(
        "--reply-hz",
        type=float,
        default=10.0,
        help="PLC→GNSS 下行发送频率（Hz），0=仅在收到上行后回一帧",
    )
    p.add_argument("--yard", default="A01", help="下行场号")
    p.add_argument("--rtg", default="RTG-001", help="下行 RTG ID")
    p.add_argument(
        "--quiet",
        action="store_true",
        help="少打印：每秒最多一行上行摘要",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="打印上行帧全部字段（含六天线、门腿、预留）",
    )
    return p.parse_args()


class PlcSession:
    def __init__(self, sock: socket.socket, addr: tuple, args: argparse.Namespace):
        self.sock = sock
        self.addr = addr
        self.args = args
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.last_uplink: dict | None = None
        self.uplink_count = 0
        self.downlink_count = 0
        self.plc_hb = 0
        self.msg_seq = 0
        self._last_print = 0.0

    def close(self) -> None:
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            pass

    def send_downlink(self) -> None:
        peer_hb = 0
        with self.lock:
            if self.last_uplink:
                peer_hb = int(self.last_uplink["heartbeat"])
            self.plc_hb = (self.plc_hb + 1) & 0xFFFF
            self.msg_seq = (self.msg_seq + 1) & 0xFFFF
            hb, seq = self.plc_hb, self.msg_seq
            gantry_mm = 0
            trolley_mm = 0
            if self.last_uplink:
                gantry_mm = abs(int(self.last_uplink["gantry_elec_dist_mm"]))
                trolley_mm = abs(int(self.last_uplink["trolley_travel_mm"]))

        frame = encode_plc_to_gnss(
            heartbeat=hb,
            peer_heartbeat=peer_hb,
            message_seq=seq,
            yard_code=self.args.yard,
            rtg_id=self.args.rtg,
            gantry_pos_mm=gantry_mm,
            trolley_pos_mm=trolley_mm,
        )
        with self.lock:
            self.sock.sendall(frame)
            self.downlink_count += 1

    def on_uplink(self, parsed: dict) -> None:
        with self.lock:
            self.last_uplink = parsed
            self.uplink_count += 1
            n = self.uplink_count

        now = time.monotonic()
        if self.args.quiet:
            if now - self._last_print < 1.0:
                return
            self._last_print = now
            print(f"[{self.addr[0]}] #{n} {format_uplink(parsed)}", flush=True)
        elif self.args.full:
            print(f"[{self.addr[0]}] ↑GNSS #{n}", flush=True)
            print(format_uplink_full(parsed), flush=True)
        else:
            print(f"[{self.addr[0]}] ↑GNSS {format_uplink(parsed)}", flush=True)

        # reply-hz=0：每收到一帧立刻回一帧
        if self.args.reply_hz <= 0:
            try:
                self.send_downlink()
            except OSError as e:
                print(f"下行发送失败: {e}", flush=True)
                self.stop.set()

    def recv_loop(self) -> None:
        self.sock.settimeout(0.2)
        while not self.stop.is_set():
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.buf.extend(chunk)
            while True:
                parsed, self.buf = try_extract_gnss_frame(self.buf)
                if parsed is None:
                    break
                self.on_uplink(parsed)
        self.stop.set()

    def reply_loop(self) -> None:
        hz = self.args.reply_hz
        if hz <= 0:
            return
        interval = 1.0 / hz
        while not self.stop.is_set():
            try:
                self.send_downlink()
            except OSError:
                break
            self.stop.wait(interval)


def handle_client(sock: socket.socket, addr: tuple, args: argparse.Namespace) -> None:
    print(f"中控已连接: {addr[0]}:{addr[1]}", flush=True)
    sess = PlcSession(sock, addr, args)
    t_recv = threading.Thread(target=sess.recv_loop, name="plc-recv", daemon=True)
    t_reply = threading.Thread(target=sess.reply_loop, name="plc-reply", daemon=True)
    t_recv.start()
    t_reply.start()
    try:
        while t_recv.is_alive() and not sess.stop.is_set():
            t_recv.join(0.5)
    finally:
        sess.close()
        t_recv.join(timeout=1.0)
        t_reply.join(timeout=1.0)
        print(
            f"连接断开 {addr[0]}:{addr[1]} | 上行={sess.uplink_count} 下行={sess.downlink_count}",
            flush=True,
        )


def main() -> int:
    args = parse_args()
    if GNSS_TO_PLC_SIZE != 158 or PLC_TO_GNSS_SIZE != 68:
        print("协议长度异常，请检查 plc_protocol_v1", file=sys.stderr)
        return 1

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((args.host, args.port))
    except OSError as e:
        print(f"绑定 {args.host}:{args.port} 失败: {e}", file=sys.stderr)
        return 1

    srv.listen(1)
    srv.settimeout(1.0)
    print(
        f"模拟 PLC 已启动（服务端）监听 {args.host}:{args.port}\n"
        f"  下行 {args.reply_hz} Hz | 场号={args.yard} RTG={args.rtg}\n"
        f"  请将 system.yaml → plc.enabled=true, plc.host=本机IP, plc.port={args.port}\n"
        f"  然后重启中控。Ctrl+C 退出。",
        flush=True,
    )

    try:
        while True:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # 同时只服务一个中控连接（现场一对一）
            handle_client(conn, addr, args)
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
