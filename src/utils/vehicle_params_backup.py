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


def persist_vehicle_params(
    cloud_config: dict,
    path: str = DEFAULT_VEHICLE_PARAMS_PATH,
) -> bool:
    """将云端大车双杆臂 / height / w_span 合并写入 vehicle_params.yaml。"""
    block = _config_block(cloud_config)
    if not block:
        return False

    l_arm = block.get("l_arm")
    l_arm_elec = block.get("l_arm_elec")
    l_arm_diesel = block.get("l_arm_diesel")
    height = block.get("height")
    w_span = block.get("w_span")
    if not (l_arm or l_arm_elec or l_arm_diesel or height is not None or w_span is not None):
        return False

    try:
        data: Dict[str, Any] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        gantry = data.get("gantry") if isinstance(data.get("gantry"), dict) else {}
        if l_arm_elec and len(l_arm_elec) >= 3:
            gantry["l_arm_elec"] = [float(x) for x in l_arm_elec[:3]]
        if l_arm_diesel and len(l_arm_diesel) >= 3:
            gantry["l_arm_diesel"] = [float(x) for x in l_arm_diesel[:3]]
        cal = block.get("l_arm_calibration")
        if isinstance(cal, dict) and cal:
            gantry["l_arm_calibration"] = cal
        if gantry:
            data["gantry"] = gantry

        if l_arm and len(l_arm) >= 3:
            data["l_arm"] = [float(x) for x in l_arm[:3]]
        if height is not None:
            data["height"] = float(height)
        if w_span is not None:
            data["w_span"] = float(w_span)
        gantry_att = block.get("gantry_attitude")
        if isinstance(gantry_att, dict) and gantry_att:
            gantry = data.get("gantry") if isinstance(data.get("gantry"), dict) else {}
            gantry["attitude"] = gantry_att
            data["gantry"] = gantry

        recv_layout = block.get("receiver_layout")
        if isinstance(recv_layout, dict) and recv_layout:
            data["receiver_layout"] = recv_layout

        trolley_cfg = block.get("trolley")
        if isinstance(trolley_cfg, dict) and trolley_cfg:
            data["trolley"] = trolley_cfg

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        logger.info(
            "车辆参数本地备份已更新: %s (elec=%s, diesel=%s, height=%s, w_span=%s, calibration=%s)",
            path,
            gantry.get("l_arm_elec"),
            gantry.get("l_arm_diesel"),
            data.get("height"),
            data.get("w_span"),
            "yes" if gantry.get("l_arm_calibration") else "no",
        )
        return True
    except Exception as e:
        logger.error("写入车辆参数备份失败: %s, error=%s", path, e)
        return False
