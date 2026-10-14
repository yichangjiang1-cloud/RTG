#!/usr/bin/env python3
"""
UDP 服务端：监听端口，接收 R61 主动推送的 NMEA。

设备配置示例（端口设置 → Socket3）：
  类型 UDP | 模式 客户端 | IP:端口 192.168.8.112:9001 | 功能 NMEA(Output)

用法：
  # 本机 IP 须与设备配置的目标 IP 一致（如 192.168.8.112）
  python tools/r61_udp_nmea_client.py
  python tools/r61_udp_nmea_client.py --port 9001 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="R61 UDP NMEA 服务端（监听接收）")
    p.add_argument(
        "--host",
        default="0.0.0.0",
        help="本机监听地址，默认 0.0.0.0 表示所有网卡",
    )
    p.add_argument(
        "--port",
        type=int,
        default=9002,
        help="本机监听端口，须与设备 Socket 目标端口一致",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="超过该秒数无数据则提示（不退出）",
    )
    return p.parse_args()


def extract_nmea_lines(buf: bytes) -> tuple[list[str], bytes]:
    lines: list[str] = []
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.decode("ascii", errors="replace").strip()
        if text.startswith("$") or text.startswith("#"):
            lines.append(text)
    for m in re.finditer(rb"\$[A-Z0-9,.*]+\*[0-9A-Fa-f]{2}", buf):
        lines.append(m.group().decode("ascii", errors="replace"))
    return lines, buf


def describe_packet(data: bytes, addr: tuple[str, int]) -> None:
    print(f"[{addr[0]}:{addr[1]}] 收到 {len(data)} 字节（非标准 NMEA 行）")
    if b"$" in data:
        text = data.decode("ascii", errors="replace")
        for line in text.replace("\r", "\n").split("\n"):
            if line.strip().startswith("$"):
                print(f"  {line.strip()}")
        return
    print(f"  hex 前 32 字节: {data[:32].hex()}")


def main() -> int:
    args = parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

    try:
        sock.bind((args.host, args.port))
    except OSError as e:
        print(
            f"绑定 {args.host}:{args.port} 失败: {e}\n"
            f"  可能端口被占用，或需管理员权限。",
            file=sys.stderr,
        )
        return 1

    sock.settimeout(1.0)
    listen_addr = sock.getsockname()

    print(f"UDP 服务端已启动，监听 {listen_addr[0]}:{listen_addr[1]}")
    print("等待 R61 推送 NMEA... (Ctrl+C 退出)")
    print("设备 Socket3 目标应填: 本机IP:9001\n")

    last_rx = time.monotonic()
    packet_count = 0
    buf = b""

    while True:
        now = time.monotonic()
        if packet_count == 0 and now - last_rx > args.timeout:
            print(
                f"\n[{args.timeout:.0f}s] 仍未收到数据。请检查：\n"
                f"  1. 设备 Socket3：UDP 客户端 → 本机IP:{args.port}\n"
                f"  2. 本机 IP 与设备配置一致（当前监听 {listen_addr[1]}）\n"
                f"  3. 设备已点「提交」，Socket3 状态为开启\n"
                f"  4. Windows 防火墙放行入站 UDP {args.port}",
                file=sys.stderr,
            )
            last_rx = now

        try:
            chunk, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue

        packet_count += 1
        last_rx = time.monotonic()
        buf += chunk

        lines, buf = extract_nmea_lines(buf)
        if lines:
            for line in lines:
                print(f"[{addr[0]}:{addr[1]}] {line}")
        else:
            describe_packet(chunk, addr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已退出")
        raise SystemExit(0)
