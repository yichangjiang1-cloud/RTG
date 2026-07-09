#!/usr/bin/env python3
"""
RTG 大车移动 + 小车相对静止 GNSS 联合模拟（9001 + 9002 + 9003）

物理模型：
  大车：四天线双接收机，默认沿堆场航向来回匀速移动（可 --gantry-static 关闭）。
  小车：双 GGA，固定在大车车体系 (body_x, body_y) 位置，随 A1 平移/转向，
        相对大车不动（rel 行程≈0，仅测大车+解算联调）。

用法（项目根目录）：
  python tools/sim_rtg_trolley_gnss.py --use-yard-origin
  python tools/sim_rtg_trolley_gnss.py --use-yard-origin --speed 0.3 --travel-m 80
  python tools/sim_rtg_trolley_gnss.py --use-yard-origin --gantry-static   # 全静止
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import sim_gantry_gnss as sg  # noqa: E402
from sim_rtg_gnss_static import build_static_layout, load_yard_origin  # noqa: E402
from sim_trolley_gnss_static import (  # noqa: E402
    build_trolley_layout_from_gantry_a1,
    build_trolley_packet,
)

DEFAULT_RATE_HZ = 2.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="大车移动 + 小车相对静止 GNSS 联合模拟（9001/9002/9003）"
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port-a1", type=int, default=9001)
    p.add_argument("--port-a2", type=int, default=9002)
    p.add_argument("--port-trolley", type=int, default=9003)
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lon", type=float, default=None)
    p.add_argument("--alt", type=float, default=sg.DEFAULT_ALT)
    p.add_argument("--heading", type=float, default=None)
    p.add_argument("--baseline-m", type=float, default=sg.DEFAULT_BASELINE_M)
    p.add_argument("--span-m", type=float, default=40.0)
    p.add_argument("--cross-side-deg", type=float, default=90.0)
    p.add_argument("--cross-x-m", type=float, default=20.0, help="小车相对 A1 车体系 X (m)")
    p.add_argument("--beam-y-m", type=float, default=15.0, help="小车相对 A1 车体系 Y (m)")
    p.add_argument("--trolley-baseline-m", type=float, default=1.0)
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ)
    p.add_argument("--count", type=int, default=0)
    p.add_argument("--no-attitude", action="store_true")
    p.add_argument(
        "--gantry-static",
        action="store_true",
        help="大车也静止（默认大车沿航向来回移动）",
    )
    p.add_argument("--speed", type=float, default=0.5, help="大车移动速度 m/s")
    p.add_argument("--travel-m", type=float, default=60.0, help="大车来回总行程 m")
    p.add_argument("--track-az", type=float, default=None, help="大车轨道方位角，默认=heading")
    p.add_argument("--use-yard-origin", action="store_true")
    p.add_argument("--yard-config", type=Path, default=Path("config/yard_config.yaml"))
    return p.parse_args()


def main() -> int:
    args = parse_args()

    lat = args.lat if args.lat is not None else sg.DEFAULT_LAT
    lon = args.lon if args.lon is not None else sg.DEFAULT_LON
    alt = args.alt
    heading = args.heading if args.heading is not None else 90.0

    if args.use_yard_origin:
        origin = load_yard_origin(args.yard_config)
        if origin:
            lat, lon, alt, heading = origin
            print(f"[config] yard origin loaded: {args.yard_config}")
        else:
            print(f"[warn] cannot read {args.yard_config}, using defaults")

    track_az = args.track_az if args.track_az is not None else heading
    motion = None if args.gantry_static else sg.OscillateMotion(
        lat, lon, args.travel_m, args.speed, track_az
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(args.rate, 0.1)
    with_att = not args.no_attitude

    gantry_mode = "static" if args.gantry_static else f"move {args.speed:.2f} m/s, travel {args.travel_m:.0f} m"
    print("=" * 68)
    print("  RTG gantry move + trolley rigid on gantry")
    print("=" * 68)
    print(f"  targets    : {args.host}:{args.port_a1} (a1)  :{args.port_a2} (a2)  :{args.port_trolley} (trolley)")
    print(f"  gantry     : {gantry_mode}  heading {heading:.1f} deg  rate {args.rate} Hz")
    print(f"  trolley    : rigid body offset X={args.cross_x_m:.1f} m  Y={args.beam_y_m:.1f} m (speed=0 rel)")
    print(f"  packet     : gantry GGAx2+RMC+PASHR, trolley GGAx2+RMC")
    print("  Ctrl+C to stop")
    print("=" * 68)

    sent = 0
    try:
        while True:
            if motion:
                a1_lat, a1_lon, hdg, gantry_spd = motion.step(interval)
            else:
                a1_lat, a1_lon, hdg, gantry_spd = lat, lon, heading, 0.0

            gantry = build_static_layout(
                a1_lat, a1_lon, alt, hdg,
                args.baseline_m, args.span_m, args.cross_side_deg,
            )
            trolley = build_trolley_layout_from_gantry_a1(
                a1_lat, a1_lon, alt, hdg,
                args.cross_x_m, args.beam_y_m,
                args.trolley_baseline_m, 270.0,
            )

            sock.sendto(
                sg.build_dual_gga_packet(gantry.recv1, gantry_spd, with_att),
                (args.host, args.port_a1),
            )
            sock.sendto(
                sg.build_dual_gga_packet(gantry.recv2, gantry_spd, with_att),
                (args.host, args.port_a2),
            )
            sock.sendto(
                build_trolley_packet(trolley.pair),
                (args.host, args.port_trolley),
            )

            sent += 1
            if sent == 1 or sent % max(int(args.rate), 1) == 0:
                c1 = trolley.pair.primary
                tag = f"spd={gantry_spd:.2f}" if motion else "static"
                print(
                    f"[{sent:5d}] gantry A1({a1_lat:.6f},{a1_lon:.6f}) {tag} | "
                    f"trolley C1({c1[0]:.6f},{c1[1]:.6f}) rel-fixed"
                )

            if args.count > 0 and sent >= args.count:
                print("packet limit reached")
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
