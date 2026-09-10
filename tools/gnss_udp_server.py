#!/usr/bin/env python3
"""
GNSS 接收机 UDP 服务端监听工具。

角色与中控一致：本机 bind 端口等待数据；接收机配置为 UDP 客户端，
向「本机局域网 IP:端口」推送 NMEA / KSXT 等语句。

用法：
  # 默认监听 9001（大车左）
  python tools/gnss_udp_server.py

  # 同时监听大车左右 + 小车
  python tools/gnss_udp_server.py --ports 9001,9002,9003

  # 指定网卡 / 落盘原始 UDP 载荷（不含时间戳/端口前缀）
  python tools/gnss_udp_server.py --host 0.0.0.0 --ports 9001 --save logs/gnss_udp.bin
"""

from __future__ import annotations

import argparse
import select
import socket
import sys
import time
from datetime import datetime
from typing import BinaryIO, Dict, List, Optional


def get_lan_ips() -> List[str]:
    """尽量列出本机局域网 IPv4，供接收机目标 IP 配置参考。"""
    ips: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="UDP 服务端：监听端口，打印接收机推送的消息"
    )
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="本机监听地址（默认 0.0.0.0 = 所有网卡）",
    )
    p.add_argument(
        "--ports",
        default="9001",
        help="监听端口，逗号分隔。例：9001 或 9001,9002,9003",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="超过该秒数无任何数据则提示（不退出）",
    )
    p.add_argument(
        "--save",
        default="",
        help="可选：追加写入原始 UDP 载荷（纯二进制，不含时间/端口前缀）",
    )
    p.add_argument(
        "--hex",
        action="store_true",
        help="非 ASCII / 无 $ 行时打印 hex 预览",
    )
    return p.parse_args()


def parse_ports(text: str) -> List[int]:
    ports: List[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        ports.append(int(part))
    if not ports:
        raise ValueError("至少指定一个端口")
    # 去重且保持顺序
    seen = set()
    out: List[int] = []
    for p in ports:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def role_label(port: int) -> str:
    return {
        9001: "gantry_a1",
        9002: "gantry_a2",
        9003: "trolley",
    }.get(port, f"port_{port}")


def bind_sockets(host: str, ports: List[int]) -> Dict[int, socket.socket]:
    socks: Dict[int, socket.socket] = {}
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if sys.platform == "win32":
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as e:
            for s in socks.values():
                s.close()
            raise OSError(
                f"绑定 {host}:{port} 失败: {e}\n"
                f"  端口可能被中控/其它程序占用，请先停掉占用进程。"
            ) from e
        sock.setblocking(False)
        socks[port] = sock
    return socks


def iter_payload_lines(data: bytes) -> List[str]:
    """拆成可打印行；$ / # 开头的语句优先。"""
    text = data.decode("ascii", errors="replace").replace("\r", "\n")
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if lines:
        return lines
    return []


def main() -> int:
    args = parse_args()
    try:
        ports = parse_ports(args.ports)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        socks = bind_sockets(args.host, ports)
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1

    save_fp: Optional[BinaryIO] = None
    if args.save:
        save_fp = open(args.save, "ab")

    lan_ips = get_lan_ips()
    print("=" * 64)
    print("GNSS UDP 服务端已启动（中控同角色：监听 / 接收机主动推送）")
    print(f"  监听地址 : {args.host}")
    print(f"  监听端口 : {', '.join(str(p) for p in ports)}")
    for p in ports:
        print(f"    - {p}  ({role_label(p)})")
    print(f"  本机参考 IP（填到接收机目标）: {', '.join(lan_ips)}")
    if args.save:
        print(f"  落盘文件 : {args.save}（仅原始 UDP 字节，控制台仍带前缀）")
    print("  Ctrl+C 退出")
    print("=" * 64)
    print()

    last_rx = time.monotonic()
    packet_count = 0
    per_port: Dict[int, int] = {p: 0 for p in ports}
    fd_to_port: Dict[int, int] = {s.fileno(): port for port, s in socks.items()}
    sock_list = list(socks.values())

    # 每秒统计：UDP 包数 / NMEA 行数
    rate_window_start = time.monotonic()
    sec_packets = 0
    sec_lines = 0
    sec_per_port: Dict[int, int] = {p: 0 for p in ports}

    def flush_rate(now: float, *, force: bool = False) -> None:
        nonlocal rate_window_start, sec_packets, sec_lines, sec_per_port
        elapsed = now - rate_window_start
        if not force and elapsed < 1.0:
            return
        if sec_packets > 0 or packet_count > 0:
            port_part = ", ".join(
                f"{role_label(p)}={sec_per_port.get(p, 0)}" for p in ports
            )
            print(
                f"[rate] 本秒消息总数: {sec_packets} 包 / {sec_lines} 行"
                + (f" ({port_part})" if len(ports) > 1 else "")
            )
        rate_window_start = now
        sec_packets = 0
        sec_lines = 0
        sec_per_port = {p: 0 for p in ports}

    try:
        while True:
            now = time.monotonic()
            flush_rate(now)

            if packet_count == 0 and now - last_rx >= args.timeout:
                print(
                    f"\n[{args.timeout:.0f}s] 仍未收到数据，请检查：\n"
                    f"  1. 接收机：UDP 客户端 → {lan_ips[0]}:{ports[0]}（端口与本脚本一致）\n"
                    f"  2. 本机与接收机同网段，防火墙放行入站 UDP {ports}\n"
                    f"  3. 中控主程序若已占用同端口，需先停止\n",
                    file=sys.stderr,
                )
                last_rx = now

            readable, _, _ = select.select(sock_list, [], [], 0.2)
            if not readable:
                continue

            for sock in readable:
                try:
                    data, addr = sock.recvfrom(8192)
                except OSError:
                    continue

                port = fd_to_port.get(sock.fileno(), 0)
                packet_count += 1
                per_port[port] = per_port.get(port, 0) + 1
                sec_packets += 1
                sec_per_port[port] = sec_per_port.get(port, 0) + 1
                last_rx = time.monotonic()
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                prefix = f"[{ts}] [{role_label(port)}:{port}] [{addr[0]}:{addr[1]}]"

                if save_fp:
                    save_fp.write(data)
                    save_fp.flush()

                lines = iter_payload_lines(data)
                if lines:
                    sec_lines += len(lines)
                    for line in lines:
                        print(f"{prefix} {line}")
                else:
                    sec_lines += 1
                    print(f"{prefix} {len(data)}B (非文本)")
                    if args.hex:
                        print(f"  hex: {data[:64].hex()}")
    except KeyboardInterrupt:
        flush_rate(time.monotonic(), force=True)
        print("\n已退出")
        if packet_count:
            stats = ", ".join(f"{role_label(p)}={n}" for p, n in per_port.items())
            print(f"共收到 {packet_count} 包 ({stats})")
    finally:
        for s in socks.values():
            s.close()
        if save_fp:
            save_fp.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
