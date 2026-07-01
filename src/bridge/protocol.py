"""
通信模块：PLC 协议定义
定义工控机 → PLC 的 TCP 二进制帧格式（小端模式，帧率 ≥ 10Hz）。

帧结构（共 96 字节）：
  [0:2]   magic      uint16  帧头魔数 0xCDAB
  [2:4]   length     uint16  数据区字节数（不含头尾）
  [4:8]   seq        uint32  帧序列号（循环递增）
  [8:16]  timestamp  double  UNIX 时间戳（秒）
  ---- 大车数据（36 字节）----
  [16:20] center_elec   float32  电气房侧作业点 L-Point X（m，字段名历史遗留）
  [20:24] center_engine float32  柴油机侧作业点 L-Point X（m，字段名历史遗留）
  [24:28] leg_lf        float32  左前腿偏移（m）
  [28:32] leg_lb        float32  左后腿偏移（m）
  [32:36] leg_rf        float32  右前腿偏移（m）
  [36:40] leg_rb        float32  右后腿偏移（m）
  [40:44] gantry_speed  float32  大车速度（m/s）
  [44:48] gantry_hdg    float32  航向角（°）
  ---- 小车数据（28 字节）----
  [48:52] trolley_x     float32  小车中心 X（m）
  [52:56] trolley_y     float32  小车中心 Y（m）
  [56:60] trolley_z     float32  小车中心 Z（m）
  [60:64] travel_dist   float32  小车行程（m）
  [64:68] trolley_speed float32  小车速度（m/s）
  ---- 状态字（4 字节）----
  [68:70] status        uint16   bit0=GNSS有效 bit1=RTK固定 bit2=惯导补位
  [70:72] fix_quality   uint16   GNSS 定位质量（0-5）
  ---- 校验（4 字节）----
  [72:76] crc32         uint32   CRC32（覆盖 [0:72]）
  总计：76 字节
"""

import struct
import zlib
from typing import Optional

from src.models.positioning import PositionResult, DataSource

# 帧头魔数（小端存储为 0xAB, 0xCD）
FRAME_MAGIC   = 0xCDAB
FRAME_FMT     = "<HHId" \
                "ffffffff" \
                "fffff" \
                "HH" \
                "I"
# 上面格式：
#   H  magic
#   H  length
#   I  seq
#   d  timestamp
#   8f 大车 8 个 float
#   5f 小车 5 个 float
#   H  status
#   H  fix_quality
#   I  crc32（占位，编码时最后填入）
FRAME_SIZE    = struct.calcsize(FRAME_FMT)   # 应为 76 字节
FRAME_CRC_OFF = FRAME_SIZE - 4              # CRC 字段偏移


def encode_frame(result: PositionResult, seq: int) -> bytes:
    """
    将 PositionResult 编码为 PLC 二进制帧。

    :param result: 解算结果
    :param seq:    帧序列号
    :return:       完整帧字节串（含 CRC32）
    """
    g = result.gantry
    t = result.trolley

    # 状态字：bit0=GNSS有效, bit1=RTK固定解, bit2=惯导补位激活
    gnss_valid   = 1 if result.gnss_fix_quality >= 1 else 0
    rtk_fixed    = 1 if result.gnss_fix_quality >= 4 else 0
    imu_coasting = 1 if result.imu_coasting_active else 0
    status = (gnss_valid) | (rtk_fixed << 1) | (imu_coasting << 2)

    # 数据区长度（帧头 magic+length+seq+timestamp = 12 字节，不含 CRC）
    data_len = FRAME_SIZE - 4 - 4   # 减去 length 字段和 CRC 字段本身

    raw = struct.pack(
        FRAME_FMT,
        FRAME_MAGIC,                      # magic
        data_len,                          # length
        seq & 0xFFFFFFFF,                  # seq
        result.timestamp,                  # timestamp (double)
        # ---- 大车 ----
        g.center_elec_side,
        g.center_engine_side,
        g.leg_offsets[0],                  # 左前
        g.leg_offsets[1],                  # 左后
        g.leg_offsets[2],                  # 右前
        g.leg_offsets[3],                  # 右后
        g.speed,
        g.heading,
        # ---- 小车 ----
        t.center_x,
        t.center_y,
        t.center_z,
        t.travel_distance,
        t.speed,
        # ---- 状态 ----
        status,
        result.gnss_fix_quality,
        # ---- CRC 占位 ----
        0,
    )

    # 计算 CRC32（覆盖除最后4字节以外的所有内容）
    crc = zlib.crc32(raw[:-4]) & 0xFFFFFFFF
    frame = raw[:-4] + struct.pack("<I", crc)
    return frame


def decode_frame(data: bytes) -> Optional[dict]:
    """
    解码 PLC 帧（用于回环测试或 PLC 应答解析）。

    :return: 字段字典，或 None（帧不合法）
    """
    if len(data) < FRAME_SIZE:
        return None

    # 验证 CRC
    crc_calc = zlib.crc32(data[:FRAME_CRC_OFF]) & 0xFFFFFFFF
    crc_recv = struct.unpack_from("<I", data, FRAME_CRC_OFF)[0]
    if crc_calc != crc_recv:
        return None

    fields = struct.unpack_from(FRAME_FMT, data)
    magic = fields[0]
    if magic != FRAME_MAGIC:
        return None

    return {
        "magic":          fields[0],
        "length":         fields[1],
        "seq":            fields[2],
        "timestamp":      fields[3],
        "center_elec":    fields[4],
        "center_engine":  fields[5],
        "leg_lf":         fields[6],
        "leg_lb":         fields[7],
        "leg_rf":         fields[8],
        "leg_rb":         fields[9],
        "gantry_speed":   fields[10],
        "gantry_hdg":     fields[11],
        "trolley_x":      fields[12],
        "trolley_y":      fields[13],
        "trolley_z":      fields[14],
        "travel_dist":    fields[15],
        "trolley_speed":  fields[16],
        "status":         fields[17],
        "fix_quality":    fields[18],
        "crc32":          fields[19],
    }

