"""
左右接收机配对时效检查。

两台接收机各自取"最新一帧"参与姿态与轮胎解算。
优先用 NMEA GGA 的 UTC 历元秒对齐；缺 UTC 时回退到帧到达墙钟时间。
时间差超限时丢弃较旧的一台，避免两侧位置错位后联合解算。
"""
from __future__ import annotations

from typing import Optional

DEFAULT_MAX_PAIR_SKEW_S = 0.25
DEFAULT_MAX_INTRA_ANTENNA_SKEW_S = 0.05
DEFAULT_TIRE_PAIR_HOLD_S = 1.0
_DAY_S = 86400.0
_TIRE_KEYS = ("elec1", "elec2", "diesel1", "diesel2")


def merge_held_tires(
    tires: dict,
    held: Optional[dict],
    held_at: float,
    now: float,
    hold_s: float,
) -> tuple[dict, bool]:
    """
    当前帧四轮胎不齐时，在 hold_s 内用上一帧齐整结果填缺。
    返回 (合并后的 tires 浅拷贝, 是否用了 hold)。
    """
    out = dict(tires or {})
    if all(out.get(k) is not None for k in _TIRE_KEYS):
        return out, False
    if not held or hold_s <= 0:
        return out, False
    if (now - float(held_at)) > float(hold_s):
        return out, False
    used = False
    for k in _TIRE_KEYS:
        if out.get(k) is None and held.get(k) is not None:
            out[k] = held[k]
            used = True
    return out, used


def utc_sod_diff_s(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """两路 UTC 当天秒差的最短弧（秒）。缺一侧返回 None。"""
    if a is None or b is None:
        return None
    try:
        da = float(a) % _DAY_S
        db = float(b) % _DAY_S
    except (TypeError, ValueError):
        return None
    d = abs(da - db)
    if d > _DAY_S * 0.5:
        d = _DAY_S - d
    return d


def apply_pair_skew(
    live1: bool,
    live2: bool,
    t1: float,
    t2: float,
    max_skew_s: float = DEFAULT_MAX_PAIR_SKEW_S,
    utc_sod1: Optional[float] = None,
    utc_sod2: Optional[float] = None,
) -> tuple[bool, bool, bool, Optional[float], str]:
    """
    :param live1/live2: 两台接收机各自是否在线
    :param t1/t2: 两台接收机最新一帧的到达时间（墙钟秒）
    :param utc_sod1/2: NMEA UTC 当天秒；齐全时优先生效
    :return: (live1, live2, dropped, dt_s, align_source)
             align_source: "utc" | "arrival" | "none"
    """
    if not (live1 and live2):
        return live1, live2, False, None, "none"

    utc_dt = utc_sod_diff_s(utc_sod1, utc_sod2)
    if utc_dt is not None:
        if utc_dt <= max_skew_s:
            return live1, live2, False, utc_dt, "utc"
        # UTC 判定较旧一侧：当天秒更小且未跨日时视作旧；跨日用到达时间兜底
        u1 = float(utc_sod1) % _DAY_S
        u2 = float(utc_sod2) % _DAY_S
        raw = abs(u1 - u2)
        if raw > _DAY_S * 0.5:
            # 跨日：用到达时间决定谁旧
            if t1 < t2:
                return False, live2, True, utc_dt, "utc"
            return live1, False, True, utc_dt, "utc"
        if u1 < u2:
            return False, live2, True, utc_dt, "utc"
        return live1, False, True, utc_dt, "utc"

    arrival_dt = abs(float(t1) - float(t2))
    if arrival_dt <= max_skew_s:
        return live1, live2, False, arrival_dt, "arrival"
    if t1 < t2:
        return False, live2, True, arrival_dt, "arrival"
    return live1, False, True, arrival_dt, "arrival"
