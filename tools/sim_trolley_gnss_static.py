#!/usr/bin/env python3
"""
小车双 GGA · 静止 GNSS 模拟（UDP → 中控 port 9003 / role=trolley）

物理模型：
  单接收机同包双 GGA → C1(主) + C2(副)，刚体基线约 1 m（沿大车横梁方向 ±0.5 m）。
  不发送 PASHR/HDT（中控对 trolley 忽略姿态句）。
  默认在大车横梁上相对静止，位置由大车 A1 参考点 + 车体系偏移推算，
  便于与 tools/sim_rtg_gnss_static.py 联调。

用法（项目根目录）：
  python tools/sim_trolley_gnss_static.py
  python tools/sim_trolley_gnss_static.py --use-yard-origin
  python tools/sim_trolley_gnss_static.py --beam-y-m 20 --cross-x-m 20
  python tools/sim_trolley_gnss_static.py --lat 30.123456 --lon 120.654321 --heading 0
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import sim_gantry_gnss as sg  # noqa: E402

DEFAULT_RATE_HZ = 2.0
DEFAULT_BASELINE_M = 1.0
# C2 在 C1 的 -车体系 Y 方向（与 vehicle_params C1/C2 L_arm ±0.5 m 一致）
DEFAULT_BASELINE_BODY_DEG = 270.0


@dataclass(frozen=True)
class TrolleyLayout:
    """C1/C2 静止布局（C1 为主天线 / GGA[0]）。"""
    pair: sg.AntennaPair
    center_lat: float
    center_lon: float
    center_alt: float
    heading_deg: float
    body_x_m: float
    body_y_m: float


def offset_gantry_body_m(
    lat: float,
    lon: float,
    alt: float,
    heading_deg: float,
    body_x_m: float,
    body_y_m: float,
    body_z_m: float = 0.0,
) -> tuple[float, float, float]:
    """
    大车车体系偏移 → WGS84（平面近似，与 gantry_frame 互逆）。
    body X 沿航向，body Y 沿横梁（小车行程方向）。
    """
    h = math.radians(heading_deg)
    c, s = math.cos(h), math.sin(h)
    dn = body_x_m * c - body_y_m * s
    de = body_x_m * s + body_y_m * c
    dlat = dn / 111_320.0
    dlon = de / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon, alt + body_z_m


def build_trolley_pair_from_center(
    center_lat: float,
    center_lon: float,
    alt: float,
    heading_deg: float,
    baseline_m: float = DEFAULT_BASELINE_M,
    baseline_body_deg: float = DEFAULT_BASELINE_BODY_DEG,
) -> sg.AntennaPair:
    """
    由 C1/C2 中点 WGS84 推算双天线：C1 = 中点 + 半基线沿 +body Y。
    """
    half = baseline_m / 2.0
    c1_lat, c1_lon, c1_alt = offset_gantry_body_m(
        center_lat, center_lon, alt, heading_deg, 0.0, half, 0.0
    )
    return sg.rigid_antenna_pair(
        c1_lat, c1_lon, c1_alt, heading_deg, baseline_m, baseline_body_deg
    )


def build_trolley_layout_from_gantry_a1(
    a1_lat: float,
    a1_lon: float,
    alt: float,
    heading_deg: float,
    body_x_m: float,
    body_y_m: float,
    baseline_m: float,
    baseline_body_deg: float,
) -> TrolleyLayout:
    """以 sim_rtg 的 A1 为参考，按车体系偏移放置小车中点。"""
    cx, cy, cz = offset_gantry_body_m(
        a1_lat, a1_lon, alt, heading_deg, body_x_m, body_y_m, 0.0
    )
    pair = build_trolley_pair_from_center(
        cx, cy, cz, heading_deg, baseline_m, baseline_body_deg
    )
    return TrolleyLayout(
        pair=pair,
        center_lat=cx,
        center_lon=cy,
        center_alt=cz,
        heading_deg=heading_deg,
        body_x_m=body_x_m,
        body_y_m=body_y_m,
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


def build_trolley_packet(pair: sg.AntennaPair) -> bytes:
    """小车包：GGA(C1) + GGA(C2) + RMC，无姿态。"""
    return sg.build_dual_gga_packet(pair, speed_mps=0.0, with_attitude=False)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="小车双 GGA 静止 GNSS 模拟（UDP 9003 / trolley）"
    )
    p.add_argument("--host", default="127.0.0.1", help="中控监听地址")
    p.add_argument("--port", type=int, default=9003, help="小车 UDP 端口")
    p.add_argument("--lat", type=float, default=None, help="C1/C2 中点纬度（直接指定时忽略 A1 参考）")
    p.add_argument("--lon", type=float, default=None, help="C1/C2 中点经度")
    p.add_argument("--alt", type=float, default=sg.DEFAULT_ALT, help="天线海拔 (m)")
    p.add_argument("--heading", type=float, default=None, help="大车航向 (°)，北=0")
    p.add_argument(
        "--cross-x-m",
        type=float,
        default=20.0,
        help="相对 A1 的车体系 X 偏移 (m)，默认 20=左右跨距中点（span 40 m）",
    )
    p.add_argument(
        "--beam-y-m",
        type=float,
        default=15.0,
        help="相对 A1 的车体系 Y 偏移 (m)，沿横梁方向",
    )
    p.add_argument("--baseline-m", type=float, default=DEFAULT_BASELINE_M, help="C1-C2 基线 (m)")
    p.add_argument(
        "--baseline-body-deg",
        type=float,
        default=DEFAULT_BASELINE_BODY_DEG,
        help="C2 相对 C1 的车体偏角 (°)，默认 270=-body Y",
    )
    p.add_argument("--rate", type=float, default=DEFAULT_RATE_HZ, help="发送频率 Hz")
    p.add_argument("--count", type=int, default=0, help="发送包数，0=一直发送")
    p.add_argument(
        "--use-yard-origin",
        action="store_true",
        help="从 config/yard_config.yaml 读取 A1 原点与 heading（与 sim_rtg 一致）",
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
    body_x = args.cross_x_m
    body_y = args.beam_y_m

    if args.use_yard_origin:
        origin = load_yard_origin(args.yard_config)
        if origin:
            lat, lon, alt, heading = origin
            print(f"[配置] 已加载堆场原点 (A1 参考): {args.yard_config}")
        else:
            print(f"[警告] 无法读取 {args.yard_config}，使用默认经纬度")

    if args.lat is not None and args.lon is not None:
        layout = TrolleyLayout(
            pair=build_trolley_pair_from_center(
                lat, lon, alt, heading, args.baseline_m, args.baseline_body_deg
            ),
            center_lat=lat,
            center_lon=lon,
            center_alt=alt,
            heading_deg=heading,
            body_x_m=0.0,
            body_y_m=0.0,
        )
    else:
        layout = build_trolley_layout_from_gantry_a1(
            lat, lon, alt, heading,
            body_x, body_y,
            args.baseline_m, args.baseline_body_deg,
        )

    pair = layout.pair
    c1_lat, c1_lon, c1_alt = pair.primary
    c2_lat, c2_lon, c2_alt = pair.secondary

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(args.rate, 0.1)

    print("=" * 68)
    print("  小车 · 双 GGA · 静止 GNSS 模拟")
    print("=" * 68)
    print(f"  目标       : {args.host}:{args.port}  (role=trolley)")
    print(f"  模式       : 静止  航向 {layout.heading_deg:.1f}°  频率 {args.rate} Hz")
    if args.lat is None or args.lon is None:
        print(f"  参考       : 大车 A1 ({lat:.8f}, {lon:.8f})")
        print(f"  车体系偏移 : X={layout.body_x_m:.1f} m  Y={layout.body_y_m:.1f} m")
    print(f"  中点       : {layout.center_lat:.8f}, {layout.center_lon:.8f}, alt={layout.center_alt:.1f} m")
    print(f"  C1 (GGA[0]): {c1_lat:.8f}, {c1_lon:.8f}")
    print(f"  C2 (GGA[1]): {c2_lat:.8f}, {c2_lon:.8f}")
    print(f"  基线       : {args.baseline_m:.2f} m  (body 偏角 {args.baseline_body_deg:.0f}°)")
    print("  每包       : GGA×2 + RMC（无姿态句）")
    print("  联调提示   : 需同时有大车解算 (9001/9002)；可与 sim_rtg_gnss_static.py 同开")
    print("  按 Ctrl+C 停止")
    print("=" * 68)

    sent = 0
    try:
        while True:
            payload = build_trolley_packet(pair)
            sock.sendto(payload, (args.host, args.port))
            sent += 1

            if sent == 1 or sent % max(int(args.rate), 1) == 0:
                print(
                    f"[{sent:5d}] → trolley "
                    f"C1({c1_lat:.6f},{c1_lon:.6f}) "
                    f"C2({c2_lat:.6f},{c2_lon:.6f})  (静止)"
                )

            if args.count > 0 and sent >= args.count:
                print("已达发送包数上限，退出")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        sock.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
