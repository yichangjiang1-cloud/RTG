#!/usr/bin/env python3
"""
RTG 大车双接收机 · 四天线 + 小车 · 静止 GNSS 模拟（UDP → 中控 GnssReceiver）

物理模型（与现场一致）：
  接收机1  gantry_a1 :9001  同包双 GGA → A1(主/电气房侧) + A2(副/柴油机侧)
  接收机2  gantry_a2 :9002  同包双 GGA → B1(主) + B2(副)
  接收机3  trolley   :9003  同包双 GGA → C1(主) + C2(副)（可用 --no-trolley 关闭）
  同机两天线为刚体基线（--baseline-m，沿车头方向）；
  左右接收机横向相距 --span-m（垂直于航向）；
  小车固定在大车车体系 (--trolley-x-m, --trolley-y-m) 处，随大车静止。

默认静止、持续发送，便于联调融合 / 解算 / 智能杆臂标定 / 四天线安装标定 / 小车零点采集。

用法（项目根目录）：
  python tools/sim_rtg_gnss_static.py
  python tools/sim_rtg_gnss_static.py --use-yard-origin
  python tools/sim_rtg_gnss_static.py --lat 30.123456 --lon 120.654321 --heading 0 --rate 5
  python tools/sim_rtg_gnss_static.py --no-trolley           # 仅大车四天线
  python tools/sim_rtg_gnss_static.py --trolley-x-m 20 --trolley-y-m 15  # 小车车体系位置
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# 复用单侧双天线 NMEA 构造
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import sim_gantry_gnss as sg  # noqa: E402
from sim_trolley_gnss_static import (  # noqa: E402
    DEFAULT_BASELINE_BODY_DEG as TROLLEY_BASELINE_BODY_DEG,
    DEFAULT_BASELINE_M as TROLLEY_BASELINE_M,
    build_trolley_layout_from_gantry_a1,
    build_trolley_packet,
)

DEFAULT_RATE_HZ = 2.0


@dataclass(frozen=True)
class FourAntennaLayout:
    """四根天线静止布局。"""
    recv1: sg.AntennaPair  # A1 + A2
    recv2: sg.AntennaPair  # B1 + B2
    span_m: float
    heading_deg: float


def build_static_layout(
    center_lat: float,
    center_lon: float,
    alt: float,
    heading_deg: float,
    baseline_m: float,
    span_m: float,
    cross_side_deg: float = 90.0,
) -> FourAntennaLayout:
    """
    center = A1 主天线（接收机1 / 电气房侧列）
    A2 = A1 + 基线沿航向
    B1 = A1 + 横向偏移 span_m（cross_side_deg 相对航向，默认 +90° = 右侧列）
    B2 = B1 + 基线沿航向
    """
    a1_lat, a1_lon = center_lat, center_lon
    pair1 = sg.rigid_antenna_pair(
        a1_lat, a1_lon, alt, heading_deg, baseline_m, baseline_body_deg=0.0
    )
    cross_az = (heading_deg + cross_side_deg) % 360.0
    b1_lat, b1_lon = sg.offset_wgs84_m(a1_lat, a1_lon, span_m, cross_az)
    pair2 = sg.rigid_antenna_pair(
        b1_lat, b1_lon, alt, heading_deg, baseline_m, baseline_body_deg=0.0
    )
    return FourAntennaLayout(
        recv1=pair1,
        recv2=pair2,
        span_m=span_m,
        heading_deg=heading_deg,
    )


def load_yard_origin(config_path: Path) -> tuple[float, float, float, float] | None:
    if yaml is None or not config_path.is_file():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        return None
    yards = data.get("yards") or []
    if not yards:
        return None
    y = yards[0]
    lat = float(y.get("origin_lat", sg.DEFAULT_LAT))
    lon = float(y.get("origin_lon", sg.DEFAULT_LON))
    alt = float(y.get("origin_alt", sg.DEFAULT_ALT))
    hdg = float(y.get("heading_deg", y.get("heading", 0.0)))
    return lat, lon, alt, hdg


def _sender_loop(
    *,
    host: str,
    port: int,
    role: str,
    pair: sg.AntennaPair,
    rate_hz: float,
    with_attitude: bool,
    max_packets: int,
    stop: threading.Event,
    stats: dict,
) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(rate_hz, 0.1)
    sent = 0
    try:
        while not stop.is_set():
            if max_packets > 0 and sent >= max_packets:
                break
            payload = sg.build_dual_gga_packet(pair, speed_mps=0.0, with_attitude=with_attitude)
            sock.sendto(payload, (host, port))
            sent += 1
            stats[role] = sent
            time.sleep(interval)
    finally:
        sock.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="大车双接收机四天线静止 GNSS 模拟（9001 + 9002）"
    )
    p.add_argument("--host", default="127.0.0.1", help="中控监听地址（发往本机）")
    p.add_argument("--port-a1", type=int, default=9001, help="接收机1 UDP 端口")
    p.add_argument("--port-a2", type=int, default=9002, help="接收机2 UDP 端口")
    p.add_argument("--port-trolley", type=int, default=9003, help="小车 UDP 端口")
    p.add_argument("--lat", type=float, default=None, help="A1 主天线纬度")
    p.add_argument("--lon", type=float, default=None, help="A1 主天线经度")
    p.add_argument("--alt", type=float, default=sg.DEFAULT_ALT, help="天线海拔 (m)")
    p.add_argument("--heading", type=float, default=None, help="整车航向 (°)，北=0")
    p.add_argument("--baseline-m", type=float, default=sg.DEFAULT_BASELINE_M, help="同机双天线基线 (m)")
    p.add_argument("--span-m", type=float, default=40.0, help="左右接收机横向间距 (m)")
    p.add_argument(
        "--cross-side-deg",
        type=float,
        default=90.0,
        help="右列相对航向的横向偏角 (°)，默认 +90",
    )
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ, help="每路发送频率 Hz")
    p.add_argument("--count", type=int, default=0, help="每路发送包数，0=一直发送")
    p.add_argument("--no-attitude", action="store_true", help="不附带 PASHR")
    p.add_argument("--no-trolley", action="store_true", help="不发送小车数据（仅大车四天线）")
    p.add_argument(
        "--trolley-x-m",
        type=float,
        default=None,
        help="小车相对 A1 的车体系 X 偏移 (m)，默认 span_m/2（左右跨距中点）",
    )
    p.add_argument(
        "--trolley-y-m",
        type=float,
        default=15.0,
        help="小车相对 A1 的车体系 Y 偏移 (m)，沿横梁方向（即小车行程零点位置）",
    )
    p.add_argument(
        "--trolley-baseline-m",
        type=float,
        default=TROLLEY_BASELINE_M,
        help="小车 C1-C2 基线 (m)",
    )
    p.add_argument(
        "--use-yard-origin",
        action="store_true",
        help="从 config/yard_config.yaml 第一个堆场读取原点与 heading",
    )
    p.add_argument(
        "--yard-config",
        type=Path,
        default=Path("config/yard_config.yaml"),
        help="堆场配置路径",
    )
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
            print(f"[配置] 已加载堆场原点: {args.yard_config}")
        else:
            print(f"[警告] 无法读取 {args.yard_config}，使用默认经纬度")

    layout = build_static_layout(
        lat, lon, alt, heading, args.baseline_m, args.span_m, args.cross_side_deg
    )

    with_trolley = not args.no_trolley
    trolley = None
    trolley_x = args.trolley_x_m if args.trolley_x_m is not None else args.span_m / 2.0
    if with_trolley:
        trolley = build_trolley_layout_from_gantry_a1(
            layout.recv1.primary[0],
            layout.recv1.primary[1],
            alt,
            heading,
            trolley_x,
            args.trolley_y_m,
            args.trolley_baseline_m,
            TROLLEY_BASELINE_BODY_DEG,
        )

    print("=" * 68)
    print("  RTG 双接收机 · 四天线 + 小车 · 静止 GNSS 模拟")
    print("=" * 68)
    trolley_target = f"  +  :{args.port_trolley} (trolley)" if with_trolley else ""
    print(f"  目标       : {args.host}:{args.port_a1} (gantry_a1)  +  :{args.port_a2} (gantry_a2){trolley_target}")
    print(f"  模式       : 静止  航向 {layout.heading_deg:.1f}°  频率 {args.rate} Hz/路")
    print(f"  A1 (9001主): {layout.recv1.primary[0]:.8f}, {layout.recv1.primary[1]:.8f}")
    print(f"  A2 (9001副): {layout.recv1.secondary[0]:.8f}, {layout.recv1.secondary[1]:.8f}")
    print(f"  B1 (9002主): {layout.recv2.primary[0]:.8f}, {layout.recv2.primary[1]:.8f}")
    print(f"  B2 (9002副): {layout.recv2.secondary[0]:.8f}, {layout.recv2.secondary[1]:.8f}")
    if with_trolley and trolley is not None:
        print(f"  C1 (9003主): {trolley.pair.primary[0]:.8f}, {trolley.pair.primary[1]:.8f}")
        print(f"  C2 (9003副): {trolley.pair.secondary[0]:.8f}, {trolley.pair.secondary[1]:.8f}")
        print(f"  小车车体系 : X={trolley.body_x_m:.1f} m  Y={trolley.body_y_m:.1f} m（相对 A1，静止）")
    print(f"  同机基线   : {args.baseline_m:.1f} m  左右跨距 {layout.span_m:.1f} m")
    print(f"  每包       : 大车 GGA×2 + RMC + {'PASHR' if not args.no_attitude else '(无PASHR)'}"
          + ("  |  小车 GGA×2 + RMC" if with_trolley else ""))
    print("  按 Ctrl+C 停止")
    print("=" * 68)

    stop = threading.Event()
    stats: dict[str, int] = {}
    with_att = not args.no_attitude

    threads = [
        threading.Thread(
            target=_sender_loop,
            name="gantry_a1",
            kwargs=dict(
                host=args.host,
                port=args.port_a1,
                role="gantry_a1",
                pair=layout.recv1,
                rate_hz=args.rate,
                with_attitude=with_att,
                max_packets=args.count,
                stop=stop,
                stats=stats,
            ),
            daemon=True,
        ),
        threading.Thread(
            target=_sender_loop,
            name="gantry_a2",
            kwargs=dict(
                host=args.host,
                port=args.port_a2,
                role="gantry_a2",
                pair=layout.recv2,
                rate_hz=args.rate,
                with_attitude=with_att,
                max_packets=args.count,
                stop=stop,
                stats=stats,
            ),
            daemon=True,
        ),
    ]
    if with_trolley and trolley is not None:
        threads.append(
            threading.Thread(
                target=_sender_loop,
                name="trolley",
                kwargs=dict(
                    host=args.host,
                    port=args.port_trolley,
                    role="trolley",
                    pair=trolley.pair,
                    rate_hz=args.rate,
                    with_attitude=False,  # 小车不发姿态
                    max_packets=args.count,
                    stop=stop,
                    stats=stats,
                ),
                daemon=True,
            )
        )
    for t in threads:
        t.start()

    tick = 0
    try:
        while True:
            time.sleep(1.0)
            tick += 1
            a1 = stats.get("gantry_a1", 0)
            a2 = stats.get("gantry_a2", 0)
            line = f"[{tick:4d}s] 已发送  gantry_a1={a1}  gantry_a2={a2}"
            if with_trolley:
                line += f"  trolley={stats.get('trolley', 0)}"
            print(line + "  (静止)")
            if args.count > 0 and a1 >= args.count and a2 >= args.count and (
                not with_trolley or stats.get("trolley", 0) >= args.count
            ):
                print("已达发送包数上限，退出")
                break
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=2.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
