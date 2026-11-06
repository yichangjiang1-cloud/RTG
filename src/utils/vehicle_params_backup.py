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


def align_calibration_arms(cal: Any, elec, diesel) -> Any:
    """标定记录里的杆臂与当前正在用的两套杆臂对齐。"""
    if not isinstance(cal, dict):
        return cal
    out = dict(cal)
    elec3 = _arm3(elec)
    diesel3 = _arm3(diesel)
    if elec3 and isinstance(out.get("elec"), dict):
        rec = dict(out["elec"])
        rec["l_arm"] = elec3
        out["elec"] = rec
    if diesel3 and isinstance(out.get("diesel"), dict):
        rec = dict(out["diesel"])
        rec["l_arm"] = diesel3
        out["diesel"] = rec
    return out


def sanitize_saved_attitude(att: Any) -> Any:
    """保存时只留 four_antenna | dual_receiver，去掉跨距类旧字段。"""
    if not isinstance(att, dict):
        return att
    out = dict(att)
    mode = str(out.get("mode") or "").strip().lower()
    out["mode"] = "dual_receiver" if mode in ("dual_receiver", "dual") else "four_antenna"
    out.pop("roll_span_m", None)
    out.pop("roll_alt0_m", None)
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

    l_arm_elec = _arm3(block.get("l_arm_elec"))
    l_arm_diesel = _arm3(block.get("l_arm_diesel"))
    cal = block.get("l_arm_calibration")
    gantry_att = block.get("gantry_attitude")
    if not (
        l_arm_elec
        or l_arm_diesel
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
        if l_arm_diesel:
            gantry["l_arm_diesel"] = l_arm_diesel
        if isinstance(cal, dict) and cal:
            gantry["l_arm_calibration"] = align_calibration_arms(
                cal, gantry.get("l_arm_elec"), gantry.get("l_arm_diesel")
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
