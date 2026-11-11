"""
大车接收机按侧分组，不按上下水端分组。

左侧接收机是 gantry_a1，右侧是 gantry_a2。哪一侧是电气房、主天线 GGA
是上游还是下游，都由 receiver_layout 决定。从天线 GGA2 是另一端。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class SideLayout:
    elec_left: bool = True
    left_primary_down: bool = True
    right_primary_down: bool = True

    def elec_primary_down(self) -> bool:
        return self.left_primary_down if self.elec_left else self.right_primary_down

    def diesel_primary_down(self) -> bool:
        return self.right_primary_down if self.elec_left else self.left_primary_down


def parse_side_layout(layout) -> SideLayout:
    """缺省：左侧电气、两侧主天线都在下游。与当前现场 9001/9002 一致。"""
    block = layout if isinstance(layout, dict) else {}
    elec = str(block.get("elec_receiver") or "left").strip().lower()

    def primary_down(key: str) -> bool:
        value = str(block.get(key) or "down").strip().lower()
        return value not in ("up", "upstream")

    return SideLayout(
        elec_left=elec != "right",
        left_primary_down=primary_down("left_primary_end"),
        right_primary_down=primary_down("right_primary_end"),
    )


def split_sides(layout: SideLayout, ga1, ga2, live1: bool, live2: bool):
    """返回电气侧、动力侧的状态，以及该侧主天线是否在下游。"""
    if layout.elec_left:
        return ga1, ga2, bool(live1), bool(live2), layout.left_primary_down, layout.right_primary_down
    return ga2, ga1, bool(live2), bool(live1), layout.right_primary_down, layout.left_primary_down


def antenna_ecef_from_dict(g: Optional[dict], primary: bool) -> Optional[Tuple[float, float, float]]:
    """接收机主 GGA 或从 GGA2 的 ECEF。没有副天线时从天线返回 None。"""
    if not g:
        return None
    if primary:
        x = float(g.get("ecef_x") or 0.0)
        y = float(g.get("ecef_y") or 0.0)
        z = float(g.get("ecef_z") or 0.0)
    else:
        if not g.get("has_antenna2"):
            return None
        x = float(g.get("antenna2_ecef_x") or 0.0)
        y = float(g.get("antenna2_ecef_y") or 0.0)
        z = float(g.get("antenna2_ecef_z") or 0.0)
    if abs(x) < 1.0 and abs(y) < 1.0:
        return None
    return x, y, z

