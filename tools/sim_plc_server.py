#!/usr/bin/env python3
"""
模拟 PLC 端（TCP 服务端），与中控机联调。

协议约定（V1.3-20261118，天线 X/Y 为有符 int32，Z 为有符 int16）：
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
  python tools/sim_plc_server.py                        # 收到一帧上行就回一帧
  python tools/sim_plc_server.py --reply-hz 10          # 改为自己按 10 Hz 发
  python tools/sim_plc_server.py --quiet                # 只打一行摘要
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
    + "iih" * 6  # V1.3：天线 X/Y 有符 int32，Z 有符 int16
    + "20H"
    + "H"
)
_PLC_BODY_FMT = "<8s10sIIhhhhhH10HH"


def _unpack_ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _pack_ascii(text: str, size: int) -> bytes:
    return (text or "").encode("ascii", errors="ignore")[:size].ljust(size, b"\x00")




# GNSS→PLC 字段 24-41，名称与协议表一致
_ANTENNA_NAMES = [
    "电气房侧GNSS1",
    "电气房侧GNSS2",
    "动力房侧GNSS1",
    "动力房侧GNSS2",
    "小车顶GNSS1",
    "小车顶GNSS2",
]

_FIX_DIGIT = {
    0: "非定位",
    1: "单点定位",
    2: "伪距差分",
    4: "固定解",
    5: "浮点解",
}
_RTG_MODE = {
    0: "不定位",
    1: "GPS定位",
    2: "组合推算定位",
}
_YARD_IN_OUT = {
    0x00: "场外",
    0x01: "场内",
    0x02: "过街场",
}
_CROSS_DEVIATION = {
    0x01: "过街场正常",
    0x02: "过街场跑偏",
    0x03: "过街场跑偏停车",
}
_YARD_KIND = {
    0: "不在场",
    1: "标准场",
    2: "过街场",
}


def _named(table: dict, value: int) -> str:
    text = table.get(int(value))
    if text is None:
        return f"{value}（表中未定义）"
    return f"{value}（{text}）"


def _fmt_fix(value: int) -> str:
    """十位、个位各表示一根天线。44 表示两根都是固定解。"""
    code = int(value) & 0xFFFF
    tens, ones = divmod(code, 10)
    tens_name = _FIX_DIGIT.get(tens, "未定义")
    ones_name = _FIX_DIGIT.get(ones, "未定义")
    return (
        f"{code}（十位 {tens}={tens_name}，个位 {ones}={ones_name}；"
        "0非定位 1单点定位 2伪距差分 4固定解 5浮点解，44为两天线都固定）"
    )


def _fmt_mm(value: int) -> str:
    return f"{int(value)} mm"


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

    # 六天线 XYZ：rest[19..36]，每根 (X int32, Y int32, Z int16)，V1.3 均为有符号
    antennas = []
    base = 19
    for i in range(6):
        x = rest[base + i * 3 + 0]
        y = rest[base + i * 3 + 1]
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
        "gantry_elec_dist_mm": rest[11],
        "gantry_diesel_dist_mm": rest[12],
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
    """按 GNSS→PLC 协议表，把收到的每个字段和含义都写出来。"""
    leg = f["leg_mm"]
    antennas = f["antennas"]
    lines = [
        "==================== 收到 GNSS→PLC（158B） ====================",
        "  1  包头 = 0xFEEF",
        "     含义：帧头，示例 0xFEEF",
        f"  2  心跳包 = {f['heartbeat']}",
        "     含义：本端每发送一次加 1",
        f"  3  对端心跳编号 = {f['peer_heartbeat']}",
        "     含义：接收到对方发来的心跳编号",
        f"  4  信息编号 = {f['message_seq']}",
        "     含义：数据包序号，以下数据每发送一次加 1",
        f"  5  电气房侧定位状态 = {_fmt_fix(f['fix_left'])}",
        "     含义：十位、个位各代表一根定位天线；0非定位 1单点定位 2伪距差分 4固定解 5浮点解，44为正常",
        f"  6  动力房侧定位状态 = {_fmt_fix(f['fix_right'])}",
        "     含义：十位、个位各代表一根定位天线；0非定位 1单点定位 2伪距差分 4固定解 5浮点解，44为正常",
        f"  7  小车顶定位状态 = {_fmt_fix(f['fix_trolley'])}",
        "     含义：十位、个位各代表一根定位天线；0非定位 1单点定位 2伪距差分 4固定解 5浮点解，44为正常",
        f"  8  RTG定位模式与状态 = {_named(_RTG_MODE, f['rtg_mode'])}",
        "     含义：0不定位 1GPS定位 2组合推算定位；不是 1 就报 GPS 定位故障",
        f"  9  当前场号 = {f['yard_code']!r}",
        "     含义：大车当前场位，如 25A、08B，字符数组 8 字节",
        f"  10 当前贝位号 = {f['bay_no']}",
        "     含义：大车当前贝位，如 10",
        f"  11 场内场外标记 = {_named(_YARD_IN_OUT, f['yard_in_out'])}",
        "     含义：场外 0x00，场内 0x01，过街场 0x02",
        f"  12 电气房侧GNSS1偏差 = {leg[0]} mm",
        "     含义：轮胎中心点与车道线偏离；偏向场内为负，偏向场外为正，单位 mm",
        f"  13 电气房侧GNSS2偏差 = {leg[1]} mm",
        "     含义：轮胎中心点与车道线偏离；偏向场内为负，偏向场外为正，单位 mm",
        f"  14 动力房侧GNSS1偏差 = {leg[2]} mm",
        "     含义：轮胎中心点与车道线偏离；偏向场内为负，偏向场外为正，单位 mm",
        f"  15 动力房侧GNSS2偏差 = {leg[3]} mm",
        "     含义：轮胎中心点与车道线偏离；偏向场内为负，偏向场外为正，单位 mm",
        f"  16 电气房侧RTG中心与起始参考线距离 = {_fmt_mm(f['gantry_elec_dist_mm'])}",
        "     含义：大车当前位置，单位 mm",
        f"  17 动力房侧RTG中心与起始参考线距离 = {_fmt_mm(f['gantry_diesel_dist_mm'])}",
        "     含义：大车当前位置，单位 mm",
        f"  18 小车中心与小车起始参考线距离 = {f['trolley_travel_mm']} mm",
        "     含义：小车当前位置，距离小车起始点的实际运行距离，单位 mm",
        f"  19 电气房侧大车速度 = {f['speed_gantry_elec_mm_s']}",
        "     含义：电气房侧大车当前速度；大车值增大方向为正，减小方向为负，单位 mm",
        f"  20 动力房侧大车速度 = {f['speed_gantry_diesel_mm_s']}",
        "     含义：动力房侧大车当前速度；大车值增大方向为正，减小方向为负，单位 mm",
        f"  21 小车速度 = {f['speed_trolley_mm_s']}",
        "     含义：小车当前速度；小车值增大方向为正，减小方向为负，单位 mm",
        f"  22 过街场偏离标志 = {_named(_CROSS_DEVIATION, f['cross_yard_deviation'])}",
        "     含义：0x01 过街场正常，0x02 过街场跑偏，0x03 过街场跑偏停车",
        f"  23 过街场标志 = {_named(_YARD_KIND, f['cross_yard_flag'])}",
        "     含义：场类型，1 标准场，2 过街场，0 不在场",
    ]
    xyz_no = 24
    for ant in antennas:
        lines.append(f"  {xyz_no:<2} {ant['name']}天线X = {_fmt_mm(ant['x_mm'])}")
        lines.append("     含义：堆场坐标 X（有符整型），朝贝位增大方向看时右侧为正，原点为堆场原点，单位 mm")
        lines.append(f"  {xyz_no + 1:<2} {ant['name']}天线Y = {_fmt_mm(ant['y_mm'])}")
        lines.append("     含义：堆场坐标 Y（有符整型），沿贝位号增大方向为正，原点为堆场原点，单位 mm")
        lines.append(f"  {xyz_no + 2:<2} {ant['name']}天线Z = {ant['z_mm']} mm")
        lines.append("     含义：相对堆场原点的高度（有符短整型），单位 mm")
        xyz_no += 3
    lines.append("  42 预留（无符短整型 × 20）")
    lines.append("     含义：预留，全部为 0，不承载大车航向")
    for i, value in enumerate(f["reserved"], start=1):
        lines.append(f"     [{i:02d}] {value}")
    lines.append("  43 包尾 = 0x0A0D")
    lines.append("     含义：帧尾，示例 0x0A0D")
    lines.append("==========================================================")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="模拟 PLC（TCP 服务端）↔ 中控")
    p.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    p.add_argument("--port", type=int, default=5000, help="监听端口，须与 plc.port 一致")
    p.add_argument(
        "--reply-hz",
        type=float,
        default=0.0,
        help="默认 0：收到一帧上行就回一帧；大于 0 则按该频率自己发",
    )
    p.add_argument("--yard", default="A01", help="下行场号")
    p.add_argument("--rtg", default="RTG-001", help="下行 RTG ID")
    p.add_argument(
        "--quiet",
        action="store_true",
        help="少打印：每秒最多一行上行摘要。默认按协议表打印收到的全部字段",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="与默认相同，按协议表打印全部字段（保留此开关以兼容旧命令）",
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

        # 先回包，再打印。控制台输出和 --quiet 的提前返回都不能把回包丢掉。
        if self.args.reply_hz <= 0:
            try:
                self.send_downlink()
            except OSError as e:
                print(f"下行发送失败: {e}", flush=True)
                self.stop.set()

        now = time.monotonic()
        if self.args.quiet:
            if now - self._last_print < 1.0:
                return
            self._last_print = now
            print(f"[{self.addr[0]}] #{n} {format_uplink(parsed)}", flush=True)
        else:
            print(f"[{self.addr[0]}] ↑GNSS #{n}", flush=True)
            print(format_uplink_full(parsed), flush=True)

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
    if args.reply_hz <= 0:
        reply_desc = "收到上行后立即回一帧"
    else:
        reply_desc = f"按 {args.reply_hz} Hz 自己发"
    print(
        f"模拟 PLC 已启动（服务端）监听 {args.host}:{args.port}\n"
        f"  下行：{reply_desc} | 场号={args.yard} RTG={args.rtg}\n"
        f"  收到的 GNSS→PLC 默认按协议表逐字段打印（加 --quiet 只打摘要）\n"
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
