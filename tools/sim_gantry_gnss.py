#!/usr/bin/env python3
"""
大车 GNSS 双天线接收机模拟（UDP → 中控 GnssReceiver）

背景：
  大车左右各 1 台双天线接收机，全车 4 根天线 A1/B1/A2/B2。
  中控按 3 路 UDP 接入：
    gantry_a1:9001 — 左侧机，同包双 GGA = A1(主) + A2(副)
    gantry_a2:9002 — 右侧机，同包双 GGA = B1(主) + B2(副)
    trolley:9003
  同一 UDP 包内按顺序发送：GGA(主天线) + GGA(副天线) + RMC + PASHR。

刚体约束：
  同机两根天线相对位置固定。副天线由主天线位置 + 车体基线向量推算：
  基线在车体坐标系中长度 --baseline-m、相对车头方向偏角 --baseline-body-deg；
  整车航向变化时，两天线一起平移/旋转，间距不变。

用法：
  python tools/sim_gantry_gnss.py                              # 默认 9001 左侧机
  python tools/sim_gantry_gnss.py --port 9001 --role gantry_a1
  python tools/sim_gantry_gnss.py --port 9002 --role gantry_a2
  python tools/sim_gantry_gnss.py --port 9002 --role gantry_a2 --move --speed 0.5 --travel-m 60
  python tools/sim_gantry_gnss.py --rate 2   # 默认 2 Hz（每秒 2 包）

  # 双接收机四天线静止联调（推荐测试）：
  python tools/sim_rtg_gnss_static.py --use-yard-origin
  .\tools\run_sim_rtg_static.ps1
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# 默认与 yard 示例原点一致
DEFAULT_LAT = 30.123456
DEFAULT_LON = 120.654321
DEFAULT_ALT = 12.0
DEFAULT_BASELINE_M = 20.0
# 副天线相对主天线：沿车体前进方向顺时针偏角（°），0=副天线在车头方向
DEFAULT_BASELINE_BODY_DEG = 0.0
DEFAULT_RATE_HZ = 2.0


def nmea_checksum(sentence_body: str) -> str:
    """sentence_body 含 leading '$'。"""
    cs = 0
    for ch in sentence_body[1:]:
        cs ^= ord(ch)
    return f"{sentence_body}*{cs:02X}\r\n"


def latlon_to_nmea(lat: float, lon: float) -> tuple[str, str, str, str]:
    """NMEA ddmm.mmmmmmm（分 7 位小数 ≈ 亚毫米级纬度分辨率）。"""
    lat_deg = int(abs(lat))
    lat_min = (abs(lat) - lat_deg) * 60.0
    lat_str = f"{lat_deg:02d}{lat_min:010.7f}"
    lat_dir = "N" if lat >= 0 else "S"

    lon_deg = int(abs(lon))
    lon_min = (abs(lon) - lon_deg) * 60.0
    lon_str = f"{lon_deg:03d}{lon_min:010.7f}"
    lon_dir = "E" if lon >= 0 else "W"
    return lat_str, lat_dir, lon_str, lon_dir


def offset_wgs84_m(
    lat: float,
    lon: float,
    distance_m: float,
    azimuth_deg: float,
) -> tuple[float, float]:
    """平面近似：沿方位角 azimuth_deg（北为 0、东为 90）偏移 distance_m。"""
    az = math.radians(azimuth_deg % 360.0)
    dn = distance_m * math.cos(az)
    de = distance_m * math.sin(az)
    dlat = dn / 111_320.0
    dlon = de / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


@dataclass
class AntennaPair:
    """同机双天线：主(定位) + 副(定向基线另一端)。"""
    primary: tuple[float, float, float]   # lat, lon, alt
    secondary: tuple[float, float, float]
    heading_deg: float
    baseline_m: float
    baseline_body_deg: float

    @property
    def baseline_world_az_deg(self) -> float:
        return (self.heading_deg + self.baseline_body_deg) % 360.0


def rigid_antenna_pair(
    primary_lat: float,
    primary_lon: float,
    alt: float,
    heading_deg: float,
    baseline_m: float,
    baseline_body_deg: float,
) -> AntennaPair:
    """
    由主天线 WGS84 + 车体基线（长度、相对航向偏角）计算副天线。
    同机为刚体：仅随主天线平移和整车航向旋转，基线长度不变。
    """
    world_az = (heading_deg + baseline_body_deg) % 360.0
    lat2, lon2 = offset_wgs84_m(primary_lat, primary_lon, baseline_m, world_az)
    return AntennaPair(
        primary=(primary_lat, primary_lon, alt),
        secondary=(lat2, lon2, alt),
        heading_deg=heading_deg,
        baseline_m=baseline_m,
        baseline_body_deg=baseline_body_deg,
    )


def build_gga(lat: float, lon: float, alt: float, fix_quality: int = 4) -> str:
    """RTK 固定解 GGA（quality=4）。"""
    now = datetime.now(timezone.utc)
    tstr = now.strftime("%H%M%S.%f")[:9]
    lat_str, lat_dir, lon_str, lon_dir = latlon_to_nmea(lat, lon)
    body = (
        f"$GNGGA,{tstr},{lat_str},{lat_dir},{lon_str},{lon_dir},"
        f"{fix_quality},14,0.8,{alt:.4f},M,0.0,M,,"
    )
    return nmea_checksum(body)


def build_pashr(heading_deg: float, roll: float = 0.0, pitch: float = 0.0) -> str:
    now = datetime.now(timezone.utc)
    tstr = now.strftime("%H%M%S.%f")[:9]
    body = (
        f"$PASHR,{tstr},{heading_deg:.2f},T,{roll:.2f},{pitch:.2f},"
        f"0.00,0,0,0.00,0"
    )
    return nmea_checksum(body)


def build_rmc(lat: float, lon: float, heading_deg: float, speed_mps: float = 0.0) -> str:
    """RMC 航迹角始终为车体航向；速度符号表示前进/后退。"""
    lat_str, lat_dir, lon_str, lon_dir = latlon_to_nmea(lat, lon)
    speed_kn = abs(speed_mps) / 0.514444
    course = heading_deg % 360.0
    now = datetime.now(timezone.utc)
    tstr = now.strftime("%H%M%S.%f")[:9]
    dstr = now.strftime("%d%m%y")
    body = (
        f"$GNRMC,{tstr},A,{lat_str},{lat_dir},{lon_str},{lon_dir},"
        f"{speed_kn:.2f},{course:.1f},{dstr},,,A"
    )
    return nmea_checksum(body)


def build_dual_gga_packet(pair: AntennaPair, speed_mps: float, with_attitude: bool) -> bytes:
    """同包双 GGA + RMC(主天线) + PASHR。"""
    lat1, lon1, alt1 = pair.primary
    lat2, lon2, alt2 = pair.secondary
    parts = [
        build_gga(lat1, lon1, alt1),
        build_gga(lat2, lon2, alt2),
        build_rmc(lat1, lon1, pair.heading_deg, speed_mps),
    ]
    if with_attitude:
        parts.append(build_pashr(pair.heading_deg))
    return "".join(parts).encode("ascii")


class OscillateMotion:
    """沿固定方位角来回匀速移动（三角波位移）。"""

    def __init__(
        self,
        center_lat: float,
        center_lon: float,
        travel_m: float,
        speed_mps: float,
        track_az_deg: float,
    ):
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.half_span = max(travel_m, 1.0) / 2.0
        self.speed = max(speed_mps, 0.01)
        self.track_az = track_az_deg
        self.offset_m = -self.half_span
        self.direction = 1

    def step(self, dt: float) -> tuple[float, float, float, float]:
        """
        返回主天线 (lat, lon, heading_deg, speed_mps)。

        RTG 沿轨道来回时车体朝向不变（heading = track_az）；
        仅速度带符号表示前进/后退。
        """
        self.offset_m += self.direction * self.speed * dt
        if self.offset_m >= self.half_span:
            self.offset_m = self.half_span
            self.direction = -1
        elif self.offset_m <= -self.half_span:
            self.offset_m = -self.half_span
            self.direction = 1

        lat, lon = offset_wgs84_m(
            self.center_lat, self.center_lon, self.offset_m, self.track_az
        )
        heading = self.track_az % 360.0
        speed_mps = self.direction * self.speed
        return lat, lon, heading, speed_mps


def load_yard_track_az(config_path: Path) -> float | None:
    if yaml is None or not config_path.is_file():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except OSError:
        return None
    for entry in data.get("yards") or []:
        heading = entry.get("heading_deg", entry.get("heading"))
        if heading is not None:
            return float(heading)
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="模拟大车单侧双天线 GNSS 接收机（同包双 GGA，刚体基线）"
    )
    p.add_argument("--host", default="127.0.0.1", help="中控监听地址")
    p.add_argument("--port", type=int, default=9001, help="UDP 端口（左侧 gantry_a1 默认 9001，右侧 9002）")
    p.add_argument("--role", default="gantry_a1", help="接收机角色：gantry_a1(左) / gantry_a2(右)")
    p.add_argument("--lat", type=float, default=DEFAULT_LAT, help="主天线初始纬度")
    p.add_argument("--lon", type=float, default=DEFAULT_LON, help="主天线初始经度")
    p.add_argument("--alt", type=float, default=DEFAULT_ALT, help="海拔 (m)")
    p.add_argument("--baseline-m", type=float, default=DEFAULT_BASELINE_M, help="同机两天线间距 (m)")
    p.add_argument(
        "--baseline-body-deg",
        type=float,
        default=DEFAULT_BASELINE_BODY_DEG,
        help="副天线相对车头方向的顺时针偏角 (°)，0=沿航向、90=车体右侧",
    )
    p.add_argument(
        "--baseline-az",
        type=float,
        default=None,
        help="已废弃：请用 --baseline-body-deg；若指定则按静止航向换算为 body 偏角",
    )
    p.add_argument("--heading", type=float, default=90.0, help="静止时整车航向 (°)")
    p.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE_HZ,
        help="发送频率 Hz（默认 2，即每秒 2 包）",
    )
    p.add_argument("--count", type=int, default=0, help="发送包数，0=一直发送")
    p.add_argument("--no-attitude", action="store_true", help="不附带 PASHR 姿态句")
    p.add_argument(
        "--move",
        action="store_true",
        help="启用来回移动模拟（以 --lat/--lon 为轨道中心，主天线沿轨道运动）",
    )
    p.add_argument("--speed", type=float, default=0.5, help="移动速度 (m/s)，仅 --move")
    p.add_argument("--travel-m", type=float, default=60.0, help="来回总行程 (m)，仅 --move")
    p.add_argument(
        "--track-az",
        type=float,
        default=None,
        help="移动轨道方位角 (°)；--move 时默认读 yard heading_deg",
    )
    p.add_argument(
        "--yard-config",
        type=Path,
        default=Path("config/yard_config.yaml"),
        help="堆场备份路径",
    )
    return p.parse_args()


def resolve_baseline_body_deg(args: argparse.Namespace, heading_deg: float) -> float:
    if args.baseline_az is not None:
        # 兼容旧参数：世界方位角 → 车体偏角
        return (args.baseline_az - heading_deg) % 360.0
    return args.baseline_body_deg


def main() -> int:
    args = parse_args()
    track_az = args.track_az
    if track_az is None and args.move:
        yard_az = load_yard_track_az(args.yard_config)
        track_az = yard_az if yard_az is not None else args.heading
    elif track_az is None:
        track_az = args.heading

    baseline_body = resolve_baseline_body_deg(args, track_az if args.move else args.heading)
    motion = (
        OscillateMotion(args.lat, args.lon, args.travel_m, args.speed, track_az)
        if args.move
        else None
    )

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(args.rate, 0.1)

    init_pair = rigid_antenna_pair(
        args.lat, args.lon, args.alt,
        track_az if args.move else args.heading,
        args.baseline_m, baseline_body,
    )

    print("=" * 60)
    print("  大车 GNSS 双天线模拟（同包双 GGA · 刚体基线）")
    print("=" * 60)
    print(f"  目标       : UDP {args.host}:{args.port}  (role={args.role})")
    if motion:
        print(f"  模式       : 来回移动  行程 {args.travel_m:.1f} m  速度 {args.speed:.2f} m/s")
        print(f"  轨道中心   : lat={args.lat:.8f}, lon={args.lon:.8f}")
        print(f"  轨道方位   : {track_az:.1f}°")
    else:
        print(f"  模式       : 静止  航向 {args.heading:.1f}°")
    print(f"  主天线 ant1: lat={init_pair.primary[0]:.8f}, lon={init_pair.primary[1]:.8f}")
    print(f"  副天线 ant2: lat={init_pair.secondary[0]:.8f}, lon={init_pair.secondary[1]:.8f}")
    print(
        f"  刚体基线   : {args.baseline_m:.1f} m, "
        f"车体偏角 {baseline_body:.1f}° (世界方位≈{init_pair.baseline_world_az_deg:.1f}°)"
    )
    print(f"  每包内容   : GGA(ant1) + GGA(ant2) + RMC + {'PASHR' if not args.no_attitude else '(无PASHR)'}")
    print(f"  频率       : {args.rate} Hz（每秒约 {args.rate:.0f} 包）")
    print("  按 Ctrl+C 停止")
    print("=" * 60)

    sent = 0
    try:
        while True:
            if motion:
                lat1, lon1, hdg, spd = motion.step(interval)
            else:
                lat1, lon1, hdg, spd = args.lat, args.lon, args.heading, 0.0

            pair = rigid_antenna_pair(
                lat1, lon1, args.alt, hdg, args.baseline_m, baseline_body
            )
            payload = build_dual_gga_packet(pair, spd, not args.no_attitude)
            sock.sendto(payload, (args.host, args.port))

            sent += 1
            if sent == 1 or sent % max(int(args.rate), 1) == 0:
                lat2, lon2, _ = pair.secondary
                move_tag = f" spd={spd:.2f}m/s hdg={hdg:.0f}°" if motion else ""
                print(
                    f"[{sent:5d}] → {args.role} "
                    f"ant1({lat1:.6f},{lon1:.6f}) "
                    f"ant2({lat2:.6f},{lon2:.6f}) "
                    f"基线={pair.baseline_m:.1f}m{move_tag}"
                )
            if args.count > 0 and sent >= args.count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
