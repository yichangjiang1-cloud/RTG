"""
车辆静态参数本地备份：云端 vehicle_configs 同步后写入 vehicle_params.yaml。
API 不可达时，中控启动从该文件加载兜底参数。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

DEFAULT_VEHICLE_PARAMS_PATH = "config/vehicle_params.yaml"


def _config_block(cloud_config: dict) -> Dict[str, Any]:
    block = cloud_config.get("config") if isinstance(cloud_config.get("config"), dict) else cloud_config
    return block if isinstance(block, dict) else {}


def _arm3(arm) -> list | None:
    if not isinstance(arm, (list, tuple)) or len(arm) < 3:
        return None
    return [float(x) for x in arm[:3]]


def align_calibration_arms(cal: Any, elec, diesel, extra: dict | None = None) -> Any:
    """标定记录里的杆臂与当前正在用的杆臂对齐。extra 的键是 elec_up 这类记录名。"""
    if not isinstance(cal, dict):
        return cal
    out = dict(cal)
    pairs = {"elec": elec, "diesel": diesel}
    if isinstance(extra, dict):
        pairs.update(extra)
    for key, arm in pairs.items():
        arm3 = _arm3(arm)
        if arm3 and isinstance(out.get(key), dict):
            rec = dict(out[key])
            rec["l_arm"] = arm3
            out[key] = rec
    return out


def sanitize_saved_attitude(att: Any) -> Any:
    """保存时只留 four_antenna | dual_receiver，去掉跨距类旧字段。"""
    if not isinstance(att, dict):
        return att
    out = dict(att)
    mode = str(out.get("mode") or "").strip().lower()
    out["mode"] = "dual_receiver" if mode in ("dual_receiver", "dual") else "four_antenna"
    out.pop("roll_span_m", None)
    body_cal = out.get("body_calibration")
    if isinstance(body_cal, dict):
        body_cal = dict(body_cal)
        body_cal.pop("span_m", None)
        out["body_calibration"] = body_cal
    return out


def persist_vehicle_params(
    cloud_config: dict,
    path: str = DEFAULT_VEHICLE_PARAMS_PATH,
) -> bool:
    """将云端双杆臂与姿态写入 vehicle_params.yaml，并清掉单杆臂 / 高度 / 跨距。"""
    block = _config_block(cloud_config)
    if not block:
        return False

    # 新报文里某一端显式为空时，不用旧的单侧杆臂去填，避免把上游的数写进下游。
    l_arm_elec_up = _arm3(block.get("l_arm_elec_up"))
    l_arm_elec_down = (
        _arm3(block.get("l_arm_elec_down"))
        if "l_arm_elec_down" in block
        else _arm3(block.get("l_arm_elec"))
    )
    l_arm_diesel_up = _arm3(block.get("l_arm_diesel_up"))
    l_arm_diesel_down = (
        _arm3(block.get("l_arm_diesel_down"))
        if "l_arm_diesel_down" in block
        else _arm3(block.get("l_arm_diesel"))
    )
    l_arm_elec = l_arm_elec_down
    l_arm_diesel = l_arm_diesel_down
    cal = block.get("l_arm_calibration")
    gantry_att = block.get("gantry_attitude")
    if not (
        l_arm_elec
        or l_arm_diesel
        or l_arm_elec_up
        or l_arm_diesel_up
        or isinstance(cal, dict)
        or isinstance(gantry_att, dict)
        or isinstance(block.get("receiver_layout"), dict)
        or isinstance(block.get("ntrip"), dict)
        or isinstance(block.get("trolley"), dict)
    ):
        return False

    try:
        data: Dict[str, Any] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        gantry = data.get("gantry") if isinstance(data.get("gantry"), dict) else {}
        if l_arm_elec:
            gantry["l_arm_elec"] = l_arm_elec
            gantry["l_arm_elec_down"] = l_arm_elec_down
        if l_arm_elec_up:
            gantry["l_arm_elec_up"] = l_arm_elec_up
        if l_arm_diesel:
            gantry["l_arm_diesel"] = l_arm_diesel
            gantry["l_arm_diesel_down"] = l_arm_diesel_down
        if l_arm_diesel_up:
            gantry["l_arm_diesel_up"] = l_arm_diesel_up
        if isinstance(cal, dict) and cal:
            gantry["l_arm_calibration"] = align_calibration_arms(
                cal,
                gantry.get("l_arm_elec"),
                gantry.get("l_arm_diesel"),
                {
                    "elec_up": gantry.get("l_arm_elec_up"),
                    "elec_down": gantry.get("l_arm_elec_down"),
                    "diesel_up": gantry.get("l_arm_diesel_up"),
                    "diesel_down": gantry.get("l_arm_diesel_down"),
                },
            )
        if isinstance(gantry_att, dict) and gantry_att:
            gantry["attitude"] = sanitize_saved_attitude(gantry_att)
        elif isinstance(gantry.get("attitude"), dict):
            gantry["attitude"] = sanitize_saved_attitude(gantry["attitude"])
        if gantry:
            data["gantry"] = gantry

        data.pop("l_arm", None)
        data.pop("height", None)
        data.pop("w_span", None)

        recv_layout = block.get("receiver_layout")
        if isinstance(recv_layout, dict) and recv_layout:
            data["receiver_layout"] = recv_layout

        ntrip_cfg = block.get("ntrip")
        if isinstance(ntrip_cfg, dict) and ntrip_cfg:
            data["ntrip"] = ntrip_cfg

        trolley_cfg = block.get("trolley")
        if isinstance(trolley_cfg, dict) and trolley_cfg:
            data["trolley"] = trolley_cfg

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(
            "车辆参数本地备份已更新: %s (elec=%s, diesel=%s, mode=%s, calibration=%s)",
            path,
            gantry.get("l_arm_elec"),
            gantry.get("l_arm_diesel"),
            (gantry.get("attitude") or {}).get("mode") if isinstance(gantry.get("attitude"), dict) else None,
            "yes" if gantry.get("l_arm_calibration") else "no",
        )
        return True
    except Exception as e:
        logger.error("写入车辆参数备份失败: %s, error=%s", path, e)
        return False
