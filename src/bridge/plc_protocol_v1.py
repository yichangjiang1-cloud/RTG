"""
港口集装箱 RTG GNSS ↔ PLC 通信协议 V1.0.0（20260422 xlsx）

- TCP，PLC 服务端，GNSS/中控客户端，小端
- 帧头 0xFEEF，帧尾 0x0A0D
- PLC→GNSS：68 字节
- GNSS→PLC：158 字节
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from src.models.positioning import PositionResult
from src.models.gnss_data import FixQuality
from src.solver.coordinate_transform import ecef_to_enu

FRAME_MAGIC = 0xFEEF
FRAME_FOOTER = 0x0A0D
PLC_YARD_CODE_MAX_LEN = 8  # 协议字段 9：场号，与 yard_id 一致

# 公共帧头：魔数 + 本端心跳 + 对端心跳 + 信息编号
_HEADER_FMT = "<HHHH"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# PLC → GNSS（68 B）
_PLC_BODY_FMT = "<8s10sIIhhhhhH10HH"
PLC_TO_GNSS_SIZE = _HEADER_SIZE + struct.calcsize(_PLC_BODY_FMT)

# GNSS → PLC（158 B）
_GNSS_BODY_FMT = (
    "<HHHH"  # 5-8 定位状态 x4
    "8s"  # 9 场号
    "HH"  # 10-11 贝位 / 场内标记
    "hhhh"  # 12-15 四轮腿偏差 mm
    "II"  # 16-17 电气/动力中心到原点平面距离 mm
    "H"  # 18 小车相对起始点行程 mm（无符号）
    "hhh"  # 19-21 速度 mm/s
    "HH"  # 22-23 过街场标志
    + "IIH" * 6  # 24-41 六天线 XYZ（X/Y uint32 mm, Z uint16 mm）
    + "20H"  # 42 预留
    + "H"  # 43 包尾
)
GNSS_TO_PLC_SIZE = _HEADER_SIZE + struct.calcsize(_GNSS_BODY_FMT)


@dataclass
class PlcToGnssFrame:
    """PLC 下发给 GNSS 的一帧（解析结果）。"""

    heartbeat: int = 0
    peer_heartbeat: int = 0
    message_seq: int = 0
    yard_code: str = ""
    rtg_id: str = ""
    gantry_pos_mm: int = 0
    trolley_pos_mm: int = 0
    speed_cmd_elec_mm_s: int = 0
    speed_cmd_diesel_mm_s: int = 0
    speed_fb_elec_mm_s: int = 0
    speed_fb_trolley_mm_s: int = 0
    speed_cmd_gantry_mm_s: int = 0
    gantry_run_status: int = 0


@dataclass
class PlcProtocolContext:
    """编码 GNSS→PLC 时的会话与参考坐标。"""

    heartbeat: int = 0
    peer_heartbeat: int = 0
    message_seq: int = 0
    vehicle_id: str = "RTG-001"
    yard_code: str = ""
    bay_no: int = 0
    yard_in_out: int = 1
    cross_yard_deviation: int = 0x01
    cross_yard_flag: int = 1
    # 六天线 XYZ 基准站来源：yard_origin=堆场原点（默认）；fixed=使用下方固定 WGS84
    base_station_mode: str = "yard_origin"
    base_lat: float = 0.0
    base_lon: float = 0.0
    base_alt: float = 0.0
    bay_spacing_m: float = 6.0
    last_plc: Optional[PlcToGnssFrame] = None


def _pack_fixed_ascii(text: str, size: int) -> bytes:
    raw = (text or "").encode("ascii", errors="ignore")[:size]
    return raw.ljust(size, b"\x00")[:size]


def _unpack_fixed_ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()


def _clamp_i16(v: int) -> int:
    return max(-32768, min(32767, int(v)))


def _clamp_u16(v: int) -> int:
    return max(0, min(65535, int(v)))


def _pack_mm_u32(v: int) -> int:
    """有符号毫米值按 uint32 位模式打包（PLC 小端）。"""
    v = max(-2147483648, min(2147483647, int(v)))
    return struct.unpack("<I", struct.pack("<i", v))[0]


def _m_to_mm(m: float) -> int:
    return int(round(float(m) * 1000.0))


def _planar_dist_mm(x_m: float, y_m: float) -> int:
    """相对堆场原点的平面距离 √(X²+Y²)，单位 mm（恒 ≥ 0）。"""
    return _m_to_mm(math.hypot(float(x_m), float(y_m)))


def _mps_to_mm_s(mps: float) -> int:
    return _clamp_i16(int(round(float(mps) * 1000.0)))


def _map_fix_digit(fix_quality: int) -> int:
    """GGA quality → 协议数字（0/1/2/4/5）。"""
    try:
        fq = FixQuality(int(fix_quality))
    except ValueError:
        return 0
    if fq == FixQuality.INVALID:
        return 0
    if fq == FixQuality.GPS_FIX:
        return 1
    if fq == FixQuality.DGPS_FIX:
        return 2
    if fq == FixQuality.RTK_FIXED:
        return 4
    if fq == FixQuality.RTK_FLOAT:
        return 5
    return 0


def encode_fix_status_pair(primary_q: int, secondary_q: int) -> int:
    """双天线定位状态：十位=天线1，个位=天线2；44=双固定解。"""
    p = _map_fix_digit(primary_q)
    s = _map_fix_digit(secondary_q)
    return _clamp_u16(p * 10 + s)


def _rtg_mode(result: PositionResult) -> int:
    if result.imu_coasting_active:
        return 2
    if result.gnss_fix_quality >= int(FixQuality.GPS_FIX):
        return 1
    return 0


def _antenna_enu_mm(
    ecef_x: float,
    ecef_y: float,
    ecef_z: float,
    base_lat: float,
    base_lon: float,
    base_alt: float,
) -> Tuple[int, int, int]:
    """天线相对基准站 ENU → (E_mm, N_mm, U_mm)。"""
    if abs(ecef_x) < 1e-6 and abs(ecef_y) < 1e-6:
        return 0, 0, 0
    e, n, u = ecef_to_enu(ecef_x, ecef_y, ecef_z, base_lat, base_lon, base_alt)
    return _m_to_mm(e), _m_to_mm(n), _m_to_mm(u)


def _antenna_triplet_from_dict(
    ant: Optional[dict],
    base_lat: float,
    base_lon: float,
    base_alt: float,
) -> Tuple[int, int, int]:
    if not ant:
        return 0, 0, 0
    ex = float(ant.get("ecef_x", 0.0))
    ey = float(ant.get("ecef_y", 0.0))
    ez = float(ant.get("ecef_z", 0.0))
    if abs(ex) < 1e-6 and abs(ey) < 1e-6:
        lat = float(ant.get("lat", 0.0))
        lon = float(ant.get("lon", 0.0))
        alt = float(ant.get("alt", 0.0))
        if abs(lat) > 1e-9 or abs(lon) > 1e-9:
            from src.solver.coordinate_transform import wgs84_to_ecef

            ex, ey, ez = wgs84_to_ecef(lat, lon, alt)
    emm, nmm, umm = _antenna_enu_mm(ex, ey, ez, base_lat, base_lon, base_alt)
    return _pack_mm_u32(emm), _pack_mm_u32(nmm), _clamp_u16(umm)


def _resolve_base_station(
    result: PositionResult,
    ctx: PlcProtocolContext,
) -> Tuple[float, float, float]:
    """
    六天线 XYZ 的基准站 WGS84。

    现场约定：默认与堆场原点一致（yard_config origin_lat/lon/alt）。
    若将来改用 RTK 基站，可设 ctx.base_station_mode='fixed' 并填入 base_lat/lon/alt。
    """
    if (
        ctx.base_station_mode == "fixed"
        and abs(ctx.base_lat) > 1e-9
        and abs(ctx.base_lon) > 1e-9
    ):
        return ctx.base_lat, ctx.base_lon, ctx.base_alt
    return (
        float(result.yard_origin_lat),
        float(result.yard_origin_lon),
        float(result.yard_origin_alt),
    )


def _estimate_bay_no(result: PositionResult, ctx: PlcProtocolContext) -> int:
    """当前贝位：与前端 RTK bay_no 一致，区间内向下一贝取整（solver 已算好）。"""
    if result.current_bay_no > 0:
        return _clamp_u16(result.current_bay_no)
    if ctx.bay_no > 0:
        return _clamp_u16(ctx.bay_no)
    return 0


def encode_gnss_to_plc(
    result: PositionResult,
    ctx: PlcProtocolContext,
) -> bytes:
    """将 PositionResult 编码为 GNSS→PLC 帧（158 B）。"""
    g = result.gantry
    t = result.trolley
    # 12-15 门腿偏差：待现场确认，暂发 0（int16 x4）
    leg_mm = [0, 0, 0, 0]
    ants = result.gantry_antennas or {}
    base_lat, base_lon, base_alt = _resolve_base_station(result, ctx)

    fq_left_p = getattr(result, "fix_quality_left", result.gnss_fix_quality)
    fq_left_s = getattr(result, "fix_quality_left_ant2", fq_left_p)
    fq_right_p = getattr(result, "fix_quality_right", result.gnss_fix_quality)
    fq_right_s = getattr(result, "fix_quality_right_ant2", fq_right_p)
    fq_tly_p = getattr(result, "fix_quality_trolley", result.gnss_fix_quality)
    fq_tly_s = getattr(result, "fix_quality_trolley_ant2", fq_tly_p)

    yard_code = (result.yard_id or "")[:PLC_YARD_CODE_MAX_LEN]
    rtg_field = ctx.vehicle_id or "RTG-001"

    ctx.heartbeat = (ctx.heartbeat + 1) & 0xFFFF
    ctx.message_seq = (ctx.message_seq + 1) & 0xFFFF

    spd_g = _mps_to_mm_s(g.speed)
    spd_t = _mps_to_mm_s(t.speed)

    # 16-17：电气/动力侧作业点到堆场原点的平面距离 √(X²+Y²)（mm）
    # 18：小车相对起始零点的行程 |travel_distance|（mm）
    #     起始零点 = 静态采集时小车相对大车停放位置（车体系 origin_body）
    gantry_elec_mm = _planar_dist_mm(g.center_elec_side, g.center_elec_y)
    gantry_dsl_mm = _planar_dist_mm(g.center_engine_side, g.center_engine_y)
    trolley_travel_mm = abs(_m_to_mm(t.travel_distance))

    a_elec1 = _antenna_triplet_from_dict(ants.get("a1"), base_lat, base_lon, base_alt)
    a_elec2 = _antenna_triplet_from_dict(ants.get("a2"), base_lat, base_lon, base_alt)
    a_dsl1 = _antenna_triplet_from_dict(ants.get("b1"), base_lat, base_lon, base_alt)
    a_dsl2 = _antenna_triplet_from_dict(ants.get("b2"), base_lat, base_lon, base_alt)
    tly1 = _antenna_triplet_from_dict(
        getattr(result, "trolley_antenna_primary", None) or {}, base_lat, base_lon, base_alt
    )
    tly2 = _antenna_triplet_from_dict(
        getattr(result, "trolley_antenna_secondary", None) or {}, base_lat, base_lon, base_alt
    )

    body = struct.pack(
        _GNSS_BODY_FMT,
        encode_fix_status_pair(fq_left_p, fq_left_s),
        encode_fix_status_pair(fq_right_p, fq_right_s),
        encode_fix_status_pair(fq_tly_p, fq_tly_s),
        _clamp_u16(_rtg_mode(result)),
        _pack_fixed_ascii(yard_code, PLC_YARD_CODE_MAX_LEN),
        _clamp_u16(_estimate_bay_no(result, ctx)),
        _clamp_u16(ctx.yard_in_out),  # 11 在场标记，默认配置待确认
        _clamp_i16(leg_mm[0]),
        _clamp_i16(leg_mm[1]),
        _clamp_i16(leg_mm[2]),
        _clamp_i16(leg_mm[3]),
        _pack_mm_u32(gantry_elec_mm),
        _pack_mm_u32(gantry_dsl_mm),
        _clamp_u16(trolley_travel_mm),
        spd_g,  # 19 电气侧大车速度（与 20 相同，符号与车体系一致）
        spd_g,  # 20 动力侧大车速度
        spd_t,  # 21 小车速度
        _clamp_u16(ctx.cross_yard_deviation),  # 22 默认待确认
        _clamp_u16(ctx.cross_yard_flag),  # 23 默认待确认
        *a_elec1,
        *a_elec2,
        *a_dsl1,
        *a_dsl2,
        *tly1,
        *tly2,
        *([0] * 20),
        FRAME_FOOTER,
    )

    header = struct.pack(
        _HEADER_FMT,
        FRAME_MAGIC,
        ctx.heartbeat,
        ctx.peer_heartbeat & 0xFFFF,
        ctx.message_seq,
    )
    frame = header + body
    if len(frame) != GNSS_TO_PLC_SIZE:
        raise ValueError(f"GNSS→PLC 帧长异常: {len(frame)} != {GNSS_TO_PLC_SIZE}")
    return frame


def decode_plc_to_gnss(data: bytes) -> Optional[PlcToGnssFrame]:
    """解析 PLC→GNSS 帧（68 B）。"""
    if len(data) < PLC_TO_GNSS_SIZE:
        return None
    chunk = data[:PLC_TO_GNSS_SIZE]
    magic, hb, peer, seq = struct.unpack_from(_HEADER_FMT, chunk, 0)
    if magic != FRAME_MAGIC:
        return None
    rest = struct.unpack_from(_PLC_BODY_FMT, chunk, _HEADER_SIZE)
    footer = rest[-1]
    if footer != FRAME_FOOTER:
        return None
    yard_code = _unpack_fixed_ascii(rest[0])
    rtg_id = _unpack_fixed_ascii(rest[1])
    return PlcToGnssFrame(
        heartbeat=hb,
        peer_heartbeat=peer,
        message_seq=seq,
        yard_code=yard_code,
        rtg_id=rtg_id,
        gantry_pos_mm=int(rest[2]),
        trolley_pos_mm=int(rest[3]),
        speed_cmd_elec_mm_s=int(rest[4]),
        speed_cmd_diesel_mm_s=int(rest[5]),
        speed_fb_elec_mm_s=int(rest[6]),
        speed_fb_trolley_mm_s=int(rest[7]),
        speed_cmd_gantry_mm_s=int(rest[8]),
        gantry_run_status=int(rest[9]),
    )


def try_extract_plc_frame(buffer: bytearray) -> Tuple[Optional[PlcToGnssFrame], bytearray]:
    """
    从 TCP 流缓冲中提取一帧 PLC→GNSS。
    返回 (frame, remaining_buffer)。
    """
    while len(buffer) >= PLC_TO_GNSS_SIZE:
        if struct.unpack_from("<H", buffer, 0)[0] != FRAME_MAGIC:
            del buffer[0]
            continue
        frame = decode_plc_to_gnss(bytes(buffer[:PLC_TO_GNSS_SIZE]))
        del buffer[:PLC_TO_GNSS_SIZE]
        if frame is not None:
            return frame, buffer
    return None, buffer
