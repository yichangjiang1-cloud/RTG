"""
左右接收机配对时效检查。

两台接收机各自取"最新一帧"参与姿态与轮胎解算，没有历元对齐。
两帧到达时间相差过大时，大车移动会让两侧位置错位（v × 时间差），
因此时间差超限时丢弃较旧的一台，不让它参与联合解算。
"""
from __future__ import annotations

DEFAULT_MAX_PAIR_SKEW_S = 0.3


def apply_pair_skew(
    live1: bool,
    live2: bool,
    t1: float,
    t2: float,
    max_skew_s: float = DEFAULT_MAX_PAIR_SKEW_S,
) -> tuple[bool, bool, bool]:
    """
    :param live1/live2: 两台接收机各自是否在线
    :param t1/t2: 两台接收机最新一帧的到达时间（秒）
    :return: (live1, live2, dropped) ；dropped 为 True 表示因时间差丢弃了较旧的一台
    """
    if not (live1 and live2):
        return live1, live2, False
    if abs(t1 - t2) <= max_skew_s:
        return live1, live2, False
    if t1 < t2:
        return False, live2, True
    return live1, False, True
