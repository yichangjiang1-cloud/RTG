"""
将 PositionResult 转为前端 RealtimeData / WebSocket 遥测格式。
与 server/schemas.RealtimeData、fronted/src/types/index.ts 对齐。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from src.models.positioning import PositionResult
from src.models.gnss_data import FixQuality

CST = timezone(timedelta(hours=8))


def _gnss_status(fix_quality: int, imu_coasting: bool) -> str:
    """映射 NMEA GGA quality → 前端状态字。

    1/2/3 = 单点/差分/PPS（非 RTK），不得标成 float（前端曾误显示为 RTK FLOAT）。
    4 = RTK 固定，5 = RTK 浮动。
    """
    if imu_coasting or fix_quality <= 0:
        return "no_signal"
    if fix_quality == int(FixQuality.RTK_FIXED):
        return "fix"
    if fix_quality == int(FixQuality.RTK_FLOAT):
        return "float"
    if fix_quality == int(FixQuality.DGPS_FIX):
        return "dgps"
    if fix_quality >= int(FixQuality.GPS_FIX):
        return "single"
    return "no_signal"


def _build_receivers(g, t, fix_quality: int, imu_coasting: bool) -> list:
    status = _gnss_status(fix_quality, imu_coasting)

    def _entry(rid: str, label: str, live: bool) -> dict:
        return {
            "id": rid,
            "label": label,
            "position": label,
            "status": status if live else "no_signal",
            "satellites": 0,
            "hdop": 0.0,
            "valid": live,
        }

    return [
        _entry("gantry_a1", "左 EVK 主 GGA → 电气侧作业点", g.recv1_live),
        _entry("gantry_a2", "右 EVK 主 GGA → 动力侧作业点", g.recv2_live),
        _entry("trolley", "小车接收机 9003", t.is_valid),
    ]


def _antenna2_live(result: PositionResult) -> bool:
    """副天线是否可用于杆臂标定（有独立 WGS84，且与主天线不同）。"""
    if result.antenna2_valid:
        return True
    lat2 = result.antenna2_latitude
    lon2 = result.antenna2_longitude
    if abs(lat2) < 1e-9 or abs(lon2) < 1e-9:
        return False
    return (
        abs(lat2 - result.latitude) > 1e-8
        or abs(lon2 - result.longitude) > 1e-8
    )


def position_result_to_realtime(result: PositionResult) -> dict:
    """组装 MQTT → 后端 → 前端 WebSocket 的 telemetry 载荷。"""
    g = result.gantry
    t = result.trolley
    legs = g.leg_offsets if len(g.leg_offsets) >= 4 else [0.0, 0.0, 0.0, 0.0]

    ts = result.timestamp
    if ts > 0:
        timestamp = datetime.fromtimestamp(ts, tz=CST).isoformat(timespec="milliseconds")
    else:
        timestamp = datetime.now(CST).isoformat(timespec="milliseconds")

    return {
        "timestamp": timestamp,
        "yard_id": result.yard_id,
        # 解算输出：大车 L-Point
        "lpoint_geo": {
            "lat": result.lpoint_latitude,
            "lon": result.lpoint_longitude,
            "alt": result.lpoint_altitude,
        },
        "local_coord": {
            "x": g.lpoint_x,
            "y": g.lpoint_y,
            "z": g.lpoint_z,
        },
        # 解算输入：参与 L-Point 的接收机主天线 WGS84（非 L-Point）
        "ref_antenna": {
            "lat": result.latitude,
            "lon": result.longitude,
            "alt": result.altitude,
            "ecef_x": result.ref_ecef_x,
            "ecef_y": result.ref_ecef_y,
            "ecef_z": result.ref_ecef_z,
            "receiver_id": result.ref_receiver_id,
        },
        # 兼容旧前端字段（= ref_antenna）
        "gps_coord": {
            "lat": result.latitude,
            "lon": result.longitude,
            "alt": result.altitude,
        },
        "heading": g.heading,
        "roll": g.roll,
        "pitch": g.pitch,
        "speed": g.speed,
        # 接收机1 双天线 WGS84（杆臂智能标定：A1=ref_antenna，A2=antenna2）
        "antenna2": {
            "lat": result.antenna2_latitude,
            "lon": result.antenna2_longitude,
            "alt": result.antenna2_altitude,
            "ecef_x": result.antenna2_ecef_x,
            "ecef_y": result.antenna2_ecef_y,
            "ecef_z": result.antenna2_ecef_z,
            "valid": _antenna2_live(result),
        },
        "status": _gnss_status(result.gnss_fix_quality, result.imu_coasting_active),
        "gantry_antennas": result.gantry_antennas or {},
        "receivers": _build_receivers(
            g, t, result.gnss_fix_quality, result.imu_coasting_active
        ),
        "gantry": {
            "receiver_left": {
                "x": g.center_elec_side,
                "y": g.center_elec_y,
                "valid": g.a1_valid,
            },
            "receiver_right": {
                "x": g.center_engine_side,
                "y": g.center_engine_y,
                "valid": g.a2_valid,
            },
            "elec_lpoint": {
                "x": g.center_elec_side,
                "y": g.center_elec_y,
                "valid": g.a1_valid,
            },
            "diesel_lpoint": {
                "x": g.center_engine_side,
                "y": g.center_engine_y,
                "valid": g.a2_valid,
            },
            # 兼容旧字段
            "elec_center": {
                "x": g.center_elec_side,
                "y": g.center_elec_y,
                "valid": g.a1_valid,
            },
            "diesel_center": {
                "x": g.center_engine_side,
                "y": g.center_engine_y,
                "valid": g.a2_valid,
            },
            "leg_offsets": {
                "fl": legs[0],
                "fr": legs[2],
                "rl": legs[1],
                "rr": legs[3],
            },
            "speed": g.speed,
        },
        "trolley": {
            "valid": t.is_valid,
            "position": {
                "x": t.rel_x,
                "y": t.rel_y,
                "z": t.rel_z,
            },
            "body_position": {
                "x": t.body_x,
                "y": t.body_y,
                "z": t.body_z,
            },
            "origin_body": {
                "x": t.origin_body_x,
                "y": t.origin_body_y,
                "z": t.origin_body_z,
            },
            "origin_mode": t.origin_mode,
            "travel": t.travel_distance,
            "speed": t.speed,
        },
    }
