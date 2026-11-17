"""任意停留修正：保存时把新填的厘米数加到当前位置已经生效的修正上。

与中控的生效规则一致：纠偏只落在记下的点前后两个贝位之间，
这两个贝位没有单独记下时修正为 0，段外也是 0。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple


def _usable_lane_points(
    points: Sequence[Tuple[float, Sequence[float]]],
) -> List[Tuple[float, Tuple[float, float, float, float]]]:
    usable: List[Tuple[float, Tuple[float, float, float, float]]] = []
    for item in points:
        if item is None or len(item) < 2 or item[1] is None or len(item[1]) < 4:
            continue
        usable.append((float(item[0]), tuple(float(v) for v in item[1][:4])))
    usable.sort(key=lambda row: row[0])
    return usable


def anchored_lane_points(
    points: Sequence[Tuple[float, Sequence[float]]],
    y_start: float,
    y_end: float,
    end_match_m: float = 0.5,
) -> List[Tuple[float, Tuple[float, float, float, float]]]:
    """与 src/solver/yard_geometry.anchored_lane_points 相同。"""
    zeros = (0.0, 0.0, 0.0, 0.0)
    usable = _usable_lane_points(points)
    start = float(y_start)
    end = float(y_end)

    def _covered(y: float) -> bool:
        return any(abs(py - y) <= end_match_m for py, _shifts in usable)

    if not _covered(start):
        usable.append((start, zeros))
    if not _covered(end):
        usable.append((end, zeros))
    usable.sort(key=lambda row: row[0])
    return usable


_BAY_MATCH_M = 0.5


def _resolve_bay_ys(bay_ys, y_end: Optional[float] = None) -> List[float]:
    """与 src/solver/yard_geometry._resolve_bay_ys 相同。"""
    if bay_ys is None:
        return []
    if isinstance(bay_ys, (int, float)) and not isinstance(bay_ys, bool):
        if y_end is None:
            return []
        raw = [float(bay_ys), float(y_end)]
    else:
        raw = [float(v) for v in bay_ys]
        if isinstance(y_end, (int, float)) and not isinstance(y_end, bool):
            raw.append(float(y_end))
    ordered = sorted(v for v in raw if math.isfinite(v))
    out: List[float] = []
    for value in ordered:
        if not out or value - out[-1] >= 0.05:
            out.append(value)
    return out


def applied_lane_shifts(
    y: float,
    points: Sequence[Tuple[float, Sequence[float]]],
    bay_ys=None,
    y_end: Optional[float] = None,
) -> Tuple[float, float, float, float]:
    """与 src/solver/yard_geometry.applied_lane_shifts 相同。"""
    zeros = (0.0, 0.0, 0.0, 0.0)
    bays = _resolve_bay_ys(bay_ys, y_end)
    usable = _usable_lane_points(points)
    if len(bays) < 2 or not usable:
        return zeros
    y = float(y)
    pinned: dict = {}
    pin_dist: dict = {}
    interior: dict = {}
    before: List[Tuple[float, Tuple[float, float, float, float]]] = []
    after: List[Tuple[float, Tuple[float, float, float, float]]] = []
    for py, shifts in usable:
        nearest = None
        best = _BAY_MATCH_M + 1.0
        for index, bay in enumerate(bays):
            dist = abs(py - bay)
            if dist <= _BAY_MATCH_M and dist < best:
                best = dist
                nearest = index
        if nearest is not None:
            if nearest not in pin_dist or best < pin_dist[nearest]:
                pinned[nearest] = shifts
                pin_dist[nearest] = best
            continue
        if py < bays[0]:
            before.append((py, shifts))
        elif py > bays[-1]:
            after.append((py, shifts))
        else:
            for index in range(len(bays) - 1):
                if bays[index] < py < bays[index + 1]:
                    interior.setdefault(index, []).append((py, shifts))
                    break

    def _at_bay(index: int) -> Tuple[float, float, float, float]:
        return pinned.get(index, zeros)

    def _on_segment(index: int, at: float) -> Tuple[float, float, float, float]:
        left = (bays[index], _at_bay(index))
        right = (bays[index + 1], _at_bay(index + 1))
        mids = interior.get(index, [])
        if not mids and left[1] == zeros and right[1] == zeros:
            return zeros
        return interpolate_lane_shifts(at, [left, *mids, right])

    if y < bays[0]:
        if not before:
            return zeros
        anchors = [*before, (bays[0], _at_bay(0))]
        if y < min(py for py, _shifts in anchors):
            return zeros
        return interpolate_lane_shifts(y, anchors)
    if y > bays[-1]:
        if not after:
            return zeros
        anchors = [(bays[-1], _at_bay(len(bays) - 1)), *after]
        if y > max(py for py, _shifts in anchors):
            return zeros
        return interpolate_lane_shifts(y, anchors)
    for index, bay in enumerate(bays):
        if abs(y - bay) <= 1e-6:
            return _at_bay(index)
    for index in range(len(bays) - 1):
        if bays[index] < y < bays[index + 1]:
            return _on_segment(index, y)
    return zeros


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
    bay_ys=None,
    diesel_bay_ys=None,
) -> Tuple[float, float, float, float]:
    """新填的数是还差的修正，加到该位置已经连出来的修正上。

    电气两个轮胎按电气侧贝位分段，动力两个轮胎按动力侧贝位分段。
    动力侧贝位不足两个时，跟电气侧同一套贝位。
    """
    zeros = (0.0, 0.0, 0.0, 0.0)
    if len(_resolve_bay_ys(bay_ys)) >= 2:
        elec = applied_lane_shifts(y, points, bay_ys)
    else:
        elec = zeros
    if diesel_bay_ys is not None and len(_resolve_bay_ys(diesel_bay_ys)) >= 2:
        diesel = applied_lane_shifts(y, points, diesel_bay_ys)
    else:
        diesel = elec
    base = (elec[0], elec[1], diesel[2], diesel[3])
    extra = tuple(float(v) for v in additional[:4])
    if len(extra) < 4:
        extra = extra + (0.0,) * (4 - len(extra))
    return tuple(a + b for a, b in zip(base, extra))  # type: ignore[return-value]
