"""
通信模块：PLC 协议（V1.0.0 xlsx）

对外保留 encode_frame / decode_frame 名称，内部实现见 plc_protocol_v1。
"""

from __future__ import annotations

from typing import Optional, Union

from src.models.positioning import PositionResult
from src.bridge.plc_protocol_v1 import (
    FRAME_MAGIC,
    FRAME_FOOTER,
    GNSS_TO_PLC_SIZE,
    PLC_TO_GNSS_SIZE,
    PlcProtocolContext,
    PlcToGnssFrame,
    encode_gnss_to_plc,
    decode_plc_to_gnss,
    try_extract_plc_frame,
)

# 兼容旧引用：FRAME_SIZE 指 GNSS→PLC 上行帧长
FRAME_SIZE = GNSS_TO_PLC_SIZE

# 默认会话上下文（BridgeNode 会替换为独立实例）
_default_ctx = PlcProtocolContext()


def encode_frame(
    result: PositionResult,
    ctx_or_seq: Union[PlcProtocolContext, int, None] = None,
) -> bytes:
    """
    将 PositionResult 编码为 GNSS→PLC 帧（158 B）。

    :param ctx_or_seq: PlcProtocolContext，或旧版 int 序列号（忽略，仅兼容）
    """
    if isinstance(ctx_or_seq, PlcProtocolContext):
        ctx = ctx_or_seq
    else:
        ctx = _default_ctx
    return encode_gnss_to_plc(result, ctx)


def decode_frame(data: bytes) -> Optional[PlcToGnssFrame]:
    """解析 PLC→GNSS 下行帧（68 B）。"""
    return decode_plc_to_gnss(data)


__all__ = [
    "FRAME_MAGIC",
    "FRAME_FOOTER",
    "FRAME_SIZE",
    "GNSS_TO_PLC_SIZE",
    "PLC_TO_GNSS_SIZE",
    "PlcProtocolContext",
    "PlcToGnssFrame",
    "encode_frame",
    "decode_frame",
    "encode_gnss_to_plc",
    "decode_plc_to_gnss",
    "try_extract_plc_frame",
]
