"""
驱动模块：NMEA 语句解析器
支持标准 NMEA 0183 句型：GGA、RMC、VTG、HDT
以及双天线接收机专有语句：$PSAT,GGA2（副天线 GGA）、#BESTPOS2A（和芯副天线位置）、
PASHR、GPTRA、GNHPR 等。
"""

import logging
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# WGS84 椭球参数（用于验证，实际坐标转换在 coordinate_transform 中）
_WGS84_A = 6378137.0
_WGS84_B = 6356752.3142


# 和芯 ASCII 日志 position type → NMEA GGA fix quality
_UNICORE_POS_TYPE_TO_FIX: dict[str, int] = {
    "NONE": 0,
    "FIXEDPOS": 1,
    "FIXEDHEIGHT": 1,
    "SINGLE": 1,
    "PSRDIFF": 2,
    "DGPS": 2,
    "WAAS": 2,
    "PROPAGATED": 1,
    "L1_FLOAT": 5,
    "NARROW_FLOAT": 5,
    "IONOFREE_FLOAT": 5,
    "L1_INT": 4,
    "WIDE_INT": 4,
    "NARROW_INT": 4,
    "RTK_FIXED": 4,
    "RTK_FLOAT": 5,
    "PPP_CONVERGING": 1,
    "PPP": 6,
}


def _unicore_pos_type_to_fix(pos_type: str, sol_status: str) -> int:
    if sol_status.upper() != "SOL_COMPUTED":
        return 0
    return _UNICORE_POS_TYPE_TO_FIX.get(pos_type.upper(), 1)


def _strip_unicore_tag_block(line: str) -> str:
    """和芯 TAG 前缀：\\s:1*78$GNGGA,... → $GNGGA,..."""
    if line.startswith("\\") and "$" in line:
        idx = line.find("$")
        if idx >= 0:
            return line[idx:]
    return line


def verify_checksum(sentence: str) -> bool:
    """
    验证 NMEA 校验和。

    :param sentence: 原始 NMEA 字符串（含 $ 和 *XX 校验码）
    :return: 校验是否通过
    """
    if "*" not in sentence:
        return False
    data, checksum = sentence.strip().lstrip("$").rsplit("*", 1)
    calc = 0
    for ch in data:
        calc ^= ord(ch)
    return calc == int(checksum[:2], 16)


def _lat_lon_to_decimal(value: str, direction: str) -> float:
    """
    将 NMEA 的 ddmm.mmmm 格式转换为十进制度。

    :param value:     如 "2234.5678"
    :param direction: "N" / "S" / "E" / "W"
    """
    if not value:
        return 0.0
    dot_idx = value.index(".")
    # 度数部分：纬度2位，经度3位
    deg_len = dot_idx - 2
    degrees = float(value[:deg_len])
    minutes = float(value[deg_len:])
    decimal = degrees + minutes / 60.0
    if direction in ("S", "W"):
        decimal = -decimal
    return decimal


def parse_unicore_bestpos2a(sentence: str) -> Optional[dict]:
    """
    解析和芯 UB482/UM 系列副天线位置 #BESTPOS2A ASCII 日志。

    例::
        #BESTPOS2A,COM1,...;SOL_COMPUTED,NARROW_INT,lat,lon,hgt,undulation,WGS84,...*crc

    分号后为十进制度 WGS84。hgt 与主天线 GGA 第 9 字段同为**正高/海拔**
   （和芯/诺瓦泰 BESTPOS 惯例）；undulation 为高程异常，仅作参考，
    不可再与 hgt 相加（固定解下 hgt 已与 GGA 对齐，再加会低约 |N|≈14 m）。
    """
    line = sentence.strip()
    upper = line.upper()
    if not line.startswith("#") or "BESTPOS2" not in upper[:12]:
        return None
    if ";" not in line:
        return None

    body = line.split(";", 1)[1]
    if "*" in body:
        body = body.rsplit("*", 1)[0]

    fields = body.split(",")
    if len(fields) < 5:
        return None

    sol_status = fields[0].strip()
    pos_type = fields[1].strip()

    try:
        lat = float(fields[2])
        lon = float(fields[3])
        hgt = float(fields[4])
        undulation = 0.0
        if len(fields) > 5 and fields[5].strip():
            undulation = float(fields[5])
        num_sats = 0
        if len(fields) > 14 and fields[14]:
            num_sats = int(float(fields[14]))
        elif len(fields) > 13 and fields[13]:
            num_sats = int(float(fields[13]))

        return {
            "type": "GGA2",
            "timestamp": time.time(),
            "lat": lat,
            "lon": lon,
            "alt": hgt,
            "undulation": undulation,
            "fix_quality": _unicore_pos_type_to_fix(pos_type, sol_status),
            "num_sats": num_sats,
            "hdop": 99.9,
        }
    except (ValueError, IndexError) as e:
        logger.debug("BESTPOS2A 解析失败: %s | 错误: %s", sentence[:60], e)
        return None


def parse_psat_gga2(sentence: str) -> Optional[dict]:
    """
    解析 R61 等接收机副天线位置语句 $PSAT,GGA2。

    格式：$PSAT,GGA2,time,lat,N/S,lon,E/W,fix,sats,hdop,alt,M,...
    字段布局与 $GPGGA 相同，但 time 占第 3 字段（索引 2），lat 从索引 3 起。
    """
    if not verify_checksum(sentence):
        logger.debug("PSAT GGA2 校验和错误: %s", sentence[:40])
        return None

    parts = sentence.split(",")
    if len(parts) < 11 or parts[1].upper() != "GGA2":
        return None

    try:
        lat = _lat_lon_to_decimal(parts[3], parts[4])
        lon = _lat_lon_to_decimal(parts[5], parts[6])
        fix_quality = int(parts[7]) if parts[7] else 0
        num_sats = int(parts[8]) if parts[8] else 0
        hdop = float(parts[9]) if parts[9] else 99.9
        alt = float(parts[10]) if parts[10] else 0.0

        return {
            "type": "GGA2",
            "timestamp": time.time(),
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "fix_quality": fix_quality,
            "num_sats": num_sats,
            "hdop": hdop,
        }
    except (ValueError, IndexError) as e:
        logger.debug("PSAT GGA2 解析失败: %s | 错误: %s", sentence[:60], e)
        return None


def parse_gga(sentence: str) -> Optional[dict]:
    """
    解析 $GPGGA / $GNGGA 语句（全球定位固定数据）。

    返回字段：
        timestamp, lat, lon, alt, fix_quality, num_sats, hdop
    """
    if not verify_checksum(sentence):
        logger.debug("GGA 校验和错误: %s", sentence[:40])
        return None

    parts = sentence.split(",")
    if len(parts) < 15:
        return None

    try:
        lat = _lat_lon_to_decimal(parts[2], parts[3])
        lon = _lat_lon_to_decimal(parts[4], parts[5])
        fix_quality = int(parts[6]) if parts[6] else 0
        num_sats = int(parts[7]) if parts[7] else 0
        hdop = float(parts[8]) if parts[8] else 99.9
        alt = float(parts[9]) if parts[9] else 0.0

        return {
            "type": "GGA",
            "timestamp": time.time(),   # 使用系统时间（chrony 已与 GPS 同步）
            "lat": lat,
            "lon": lon,
            "alt": alt,
            "fix_quality": fix_quality,
            "num_sats": num_sats,
            "hdop": hdop,
            "raw": sentence.strip(),
        }
    except (ValueError, IndexError) as e:
        logger.debug("GGA 解析失败: %s | 错误: %s", sentence[:60], e)
        return None


def parse_rmc(sentence: str) -> Optional[dict]:
    """
    解析 $GPRMC / $GNRMC 语句（推荐定位信息）。

    返回字段：
        timestamp, lat, lon, speed_mps, course_deg, is_valid
    """
    if not verify_checksum(sentence):
        return None

    parts = sentence.split(",")
    if len(parts) < 10:
        return None

    try:
        status = parts[2]  # 'A'=有效, 'V'=无效
        lat = _lat_lon_to_decimal(parts[3], parts[4])
        lon = _lat_lon_to_decimal(parts[5], parts[6])
        speed_knots = float(parts[7]) if parts[7] else 0.0
        course_deg = float(parts[8]) if parts[8] else 0.0

        return {
            "type": "RMC",
            "timestamp": time.time(),
            "lat": lat,
            "lon": lon,
            "speed_mps": speed_knots * 0.514444,    # 节→米/秒
            "course_deg": course_deg,
            "is_valid": (status == "A"),
        }
    except (ValueError, IndexError) as e:
        logger.debug("RMC 解析失败: %s | 错误: %s", sentence[:60], e)
        return None


def parse_vtg(sentence: str) -> Optional[dict]:
    """
    解析 $GxVTG 地面航迹与速度。

    例：$GNVTG,231.65,T,,M,0.06,N,0.11,K,R*35
    优先用 km/h 字段，缺省则用节。
    """
    if not verify_checksum(sentence):
        return None

    parts = sentence.split(",")
    if len(parts) < 8:
        return None

    try:
        course = float(parts[1]) if parts[1] else 0.0
        speed_mps = 0.0
        # parts[7]=km/h 数值，parts[8]=K；parts[5]=节，parts[6]=N
        if len(parts) > 7 and parts[7]:
            speed_mps = float(parts[7]) / 3.6
        elif parts[5]:
            speed_mps = float(parts[5]) * 0.514444
        return {
            "type": "VTG",
            "timestamp": time.time(),
            "speed_mps": speed_mps,
            "course_deg": course,
        }
    except (ValueError, IndexError) as e:
        logger.debug("VTG 解析失败: %s | 错误: %s", sentence[:60], e)
        return None


def parse_hdt(sentence: str) -> Optional[dict]:
    """
    解析 $GPHDT / $GNHDT 语句（真北航向角）。

    返回字段：
        timestamp, heading_deg
    """
    if not verify_checksum(sentence):
        return None

    parts = sentence.split(",")
    if len(parts) < 3:
        return None

    try:
        heading = float(parts[1]) if parts[1] else 0.0
        return {
            "type": "HDT",
            "timestamp": time.time(),
            "heading_deg": heading,
        }
    except (ValueError, IndexError):
        return None


def parse_attitude(sentence: str) -> Optional[dict]:
    """
    解析双天线接收机专有姿态语句（含 Heading/Pitch/Roll）。

    支持格式：
      $PTNLA,HV,...       （天宝专有）
      $GPTRA,...          （u-blox 专有）
      $PASHR,...          （通用 PASHR）
      $GNHPR / $GPHPR     （航向/俯仰/横滚，MC10 等）

    返回字段：
        timestamp, heading_deg, pitch_deg, roll_deg, heading_accuracy
    """
    if not verify_checksum(sentence):
        return None

    sentence_upper = sentence.upper()
    parts = sentence.split(",")

    try:
        # ---- PASHR 格式: $PASHR,hhmmss.ss,heading,T,roll,pitch,heave,... ----
        if sentence_upper.startswith("$PASHR"):
            if len(parts) < 7:
                return None
            heading = float(parts[2]) if parts[2] else 0.0
            roll = float(parts[4]) if parts[4] else 0.0
            pitch = float(parts[5]) if parts[5] else 0.0
            return {
                "type": "ATTITUDE",
                "timestamp": time.time(),
                "heading_deg": heading,
                "pitch_deg": pitch,
                "roll_deg": roll,
                "heading_accuracy": 0.1,    # 默认精度（°）
            }

        # ---- GxTRA: $GPTRA / $GNTRA,time,heading,pitch,roll?... ----
        # UB482: $GNTRA,time,heading,pitch,roll?,quality,sats,...
        if "TRA" in sentence_upper[:8]:
            if len(parts) < 5:
                return None
            heading = float(parts[2]) if parts[2] else 0.0
            pitch = float(parts[3]) if parts[3] else 0.0
            roll = float(parts[4]) if parts[4] else 0.0
            return {
                "type": "ATTITUDE",
                "timestamp": time.time(),
                "heading_deg": heading,
                "pitch_deg": pitch,
                "roll_deg": roll,
                "heading_accuracy": 0.1,
            }

        # ---- GxHPR: $GNHPR,time,heading,pitch,roll,quality,sats,... ----
        if "HPR" in sentence_upper[:8]:
            if len(parts) < 5:
                return None
            heading = float(parts[2]) if parts[2] else 0.0
            pitch = float(parts[3]) if parts[3] else 0.0
            roll = float(parts[4]) if parts[4] else 0.0
            return {
                "type": "ATTITUDE",
                "timestamp": time.time(),
                "heading_deg": heading,
                "pitch_deg": pitch,
                "roll_deg": roll,
                "heading_accuracy": 0.1,
            }

    except (ValueError, IndexError) as e:
        logger.debug("姿态语句解析失败: %s | 错误: %s", sentence[:60], e)

    return None


def parse_sentence(sentence: str) -> Optional[dict]:
    """
    自动识别并解析任意 NMEA / 和芯 ASCII 语句的统一入口。

    :param sentence: 原始 NMEA 或 #BESTPOS2A 字符串
    :return: 解析结果字典，或 None（未知/校验失败）
    """
    sentence = _strip_unicore_tag_block(sentence.strip())
    if sentence.startswith("#"):
        if "BESTPOS2" in sentence.upper()[:12]:
            return parse_unicore_bestpos2a(sentence)
        return None

    if not sentence.startswith("$"):
        return None

    upper = sentence.upper()
    if upper.startswith("$PSAT") and ",GGA2," in upper:
        return parse_psat_gga2(sentence)
    if "GGA" in upper[:10]:
        return parse_gga(sentence)
    if "RMC" in upper[:10]:
        return parse_rmc(sentence)
    if "VTG" in upper[:10]:
        return parse_vtg(sentence)
    if "HDT" in upper[:10]:
        return parse_hdt(sentence)
    if (
        any(kw in upper[:12] for kw in ("PASHR", "GPTRA", "GNTRA", "PTNLA"))
        or "HPR" in upper[:8]
        or "TRA" in upper[:8]
    ):
        return parse_attitude(sentence)

    return None

