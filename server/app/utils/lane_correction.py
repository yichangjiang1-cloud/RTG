"""任意停留修正：保存时把新填的厘米数加到当前位置已经生效的修正上。"""
from __future__ import annotations

from typing import Sequence, Tuple


def interpolate_lane_shifts(
    y: float,
    points: Sequence[Tuple[float, Sequence[float]]],
) -> Tuple[float, float, float, float]:
    """与中控 src/solver/yard_geometry.interpolate_lane_shifts 相同。"""
    usable = [
        (float(py), tuple(float(v) for v in shifts[:4]))
        for py, shifts in points
        if shifts is not None and len(shifts) >= 4
    ]
    zeros = (0.0, 0.0, 0.0, 0.0)
    if not usable:
        return zeros
    usable.sort(key=lambda item: item[0])
    if len(usable) == 1 or float(y) <= usable[0][0]:
        return usable[0][1]  # type: ignore[return-value]
    if float(y) >= usable[-1][0]:
        return usable[-1][1]  # type: ignore[return-value]
    for i in range(1, len(usable)):
        y0, s0 = usable[i - 1]
        y1, s1 = usable[i]
        if float(y) <= y1:
            span = y1 - y0
            if abs(span) < 1e-9:
                return s1  # type: ignore[return-value]
            t = (float(y) - y0) / span
            return tuple(a + (b - a) * t for a, b in zip(s0, s1))  # type: ignore[return-value]
    return usable[-1][1]  # type: ignore[return-value]


def absolute_with_additional(
    y: float,
    points: Sequence[Tuple[float, Sequence[float]]],
    additional: Sequence[float],
) -> Tuple[float, float, float, float]:
    """新填的数是还差的修正，加到该位置已经生效的累计值上。"""
    base = interpolate_lane_shifts(y, points)
    extra = tuple(float(v) for v in additional[:4])
    if len(extra) < 4:
        extra = extra + (0.0,) * (4 - len(extra))
    return tuple(a + b for a, b in zip(base, extra))  # type: ignore[return-value]
