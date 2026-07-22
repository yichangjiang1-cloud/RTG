"""堆场 ID 校验：与 PLC 场号字段一致（≤8 字节 ASCII）。"""

PLC_YARD_ID_MAX_LEN = 8


def validate_yard_id(yard_id: str) -> str:
    text = (yard_id or "").strip()
    if not text:
        raise ValueError("yard_id 不能为空")
    if len(text.encode("ascii", errors="strict")) > PLC_YARD_ID_MAX_LEN:
        raise ValueError(f"yard_id 最多 {PLC_YARD_ID_MAX_LEN} 个 ASCII 字符（PLC 场号）")
    try:
        text.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("yard_id 仅支持 ASCII 字符（PLC 场号）") from exc
    return text
