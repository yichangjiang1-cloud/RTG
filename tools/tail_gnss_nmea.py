#!/usr/bin/env python3
"""
实时跟踪中控机落盘的 GNSS 原始 NMEA 文件（tail -f）。

中控机运行时，原始数据写入 logs/gnss_raw/receiver_*/*.nmea。
本工具在另一终端运行，不占 UDP 端口。

用法：
  python tools/tail_gnss_nmea.py
  python tools/tail_gnss_nmea.py --receiver gantry_a1
  python tools/tail_gnss_nmea.py --dir logs/gnss_raw
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_RECEIVER_DIRS = {
    "gantry_a1": "receiver_1_gantry_a1",
    "gantry_a2": "receiver_2_gantry_a2",
    "trolley": "receiver_3_trolley",
    "a1": "receiver_1_gantry_a1",
    "a2": "receiver_2_gantry_a2",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="跟踪 GNSS 原始 NMEA 落盘文件")
    p.add_argument(
        "--dir",
        default="logs/gnss_raw",
        help="gnss.raw_log.dir，默认 logs/gnss_raw",
    )
    p.add_argument(
        "--receiver",
        choices=[*list(_RECEIVER_DIRS.keys()), "all"],
        default="all",
        help="跟踪哪一路接收机，默认 all",
    )
    p.add_argument(
        "--poll",
        type=float,
        default=0.2,
        help="轮询间隔（秒）",
    )
    return p.parse_args()


def latest_nmea_file(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.nmea"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def tail_file(path: Path, offset: int, label: str) -> int:
    try:
        with path.open("r", encoding="ascii", errors="replace") as fh:
            fh.seek(offset)
            chunk = fh.read()
            if not chunk:
                return offset
            for line in chunk.splitlines():
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                print(f"[{label}] {text}", flush=True)
            return fh.tell()
    except OSError as e:
        print(f"[{label}] 读取失败 {path}: {e}", file=sys.stderr, flush=True)
        return offset


def main() -> int:
    args = parse_args()
    base = Path(args.dir)
    if not base.is_dir():
        print(f"目录不存在: {base}（请先启动中控机并确保 gnss.raw_log.enabled=true）", file=sys.stderr)
        return 1

    if args.receiver == "all":
        targets = [(rid, base / sub) for rid, sub in _RECEIVER_DIRS.items() if rid in ("gantry_a1", "gantry_a2", "trolley")]
    else:
        sub = _RECEIVER_DIRS[args.receiver]
        rid = args.receiver if args.receiver in ("gantry_a1", "gantry_a2", "trolley") else args.receiver
        targets = [(rid, base / sub)]

    print(f"跟踪 {base} | 接收机={args.receiver} | Ctrl+C 退出", flush=True)

    state: dict[str, tuple[Path | None, int]] = {rid: (None, 0) for rid, _ in targets}

    try:
        while True:
            for rid, folder in targets:
                path = latest_nmea_file(folder)
                prev_path, offset = state[rid]
                if path is None:
                    continue
                if path != prev_path:
                    state[rid] = (path, 0)
                    print(f"[{rid}] 新会话 → {path.name}", flush=True)
                    prev_path, offset = state[rid]
                offset = tail_file(path, offset, rid)
                state[rid] = (path, offset)
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\n已退出", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
