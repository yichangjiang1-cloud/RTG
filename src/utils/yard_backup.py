"""
堆场数据本地备份：从后端 API 拉取 yards + RTK 贝点 + 车辆校准，写入 yard_config.yaml。
API 不可用时从该文件恢复。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.bridge.api_client import ApiClient

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))
DEFAULT_BACKUP_PATH = "config/yard_config.yaml"


def normalize_api_yard(item: dict) -> Optional[dict]:
    """将 GET /api/yard/all_origins 单项转为 yards 表扁平字段。"""
    yard_id = item.get("yard_id")
    if not yard_id:
        return None
    origin = item.get("origin")
    if isinstance(origin, (list, tuple)) and len(origin) >= 2:
        origin_lon = float(origin[0])
        origin_lat = float(origin[1])
        origin_alt = float(origin[2]) if len(origin) > 2 else 0.0
    else:
        origin_lat = float(item.get("origin_lat", 0.0))
        origin_lon = float(item.get("origin_lon", 0.0))
        origin_alt = float(item.get("origin_alt", 0.0))
    return {
        "yard_id": yard_id,
        "name": item.get("name", ""),
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "origin_alt": origin_alt,
        "heading_deg": float(item.get("heading_deg", item.get("heading", 0.0))),
        "bay_spacing": float(item.get("bay_spacing", 6.0)),
        "bay_count": int(item.get("bay_count", item.get("total_bays", 0))),
        "mapped": bool(item.get("mapped", False)),
        "rtk_point_count": int(item.get("rtk_point_count", 0)),
    }


def fetch_from_api(api: "ApiClient", vehicle_id: str) -> Optional[dict]:
    """从后端拉取完整堆场备份（元数据 + 贝点 + 本车校准）。"""
    yard_list = api.get_all_yards()
    if not yard_list:
        logger.warning("API 堆场列表为空，无法构建备份")
        return None

    yards = []
    yard_data: Dict[str, Any] = {}
    for item in yard_list:
        normalized = normalize_api_yard(item)
        if not normalized:
            continue
        yard_id = normalized["yard_id"]
        yards.append(normalized)

        map_payload: Dict[str, Any] = {}
        if vehicle_id:
            raw_map = api.get_yard_map(yard_id, vehicle_id)
            if raw_map:
                map_payload = dict(raw_map)
                m_payload = map_payload.get("m_matrix") or {}
                source_vid = (
                    map_payload.get("source_vehicle_id")
                    or (m_payload.get("source_vehicle_id") if isinstance(m_payload, dict) else None)
                )
                if source_vid and source_vid != vehicle_id:
                    src_list = api.get_transform_list(source_vid, yard_id)
                    if src_list:
                        map_payload["source_transform_list"] = src_list
                elif map_payload.get("transform_list"):
                    map_payload["source_transform_list"] = map_payload["transform_list"]

        yard_data[yard_id] = {
            "has_rtk_map": bool(map_payload.get("has_rtk_map")),
            "rtk_point_count": int(map_payload.get("rtk_point_count", 0)),
            "survey_points": map_payload.get("survey_points") or [],
            "calibration_status": map_payload.get("calibration_status", "none"),
            "calibration_mode": map_payload.get("calibration_mode", "none"),
            "m_matrix": map_payload.get("m_matrix"),
            "source_vehicle_id": map_payload.get("source_vehicle_id"),
            "transform_list": map_payload.get("transform_list") or [],
            "source_transform_list": map_payload.get("source_transform_list") or [],
        }

    return {
        "meta": {
            "synced_at": datetime.now(CST).isoformat(timespec="seconds"),
            "vehicle_id": vehicle_id,
            "source": "api",
        },
        "yards": yards,
        "yard_data": yard_data,
    }


def load_backup(path: str = DEFAULT_BACKUP_PATH) -> dict:
    """读取本地备份 YAML。"""
    p = Path(path)
    if not p.exists():
        logger.warning("堆场备份文件不存在: %s", path)
        return {"yards": [], "yard_data": {}}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {"yards": [], "yard_data": {}}
        data.setdefault("yards", [])
        data.setdefault("yard_data", {})
        return data
    except Exception as e:
        logger.error("读取堆场备份失败: %s", e)
        return {"yards": [], "yard_data": {}}


def save_backup(data: dict, path: str = DEFAULT_BACKUP_PATH) -> bool:
    """写入本地备份 YAML。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        meta = data.get("meta") or {}
        if not meta.get("synced_at"):
            meta["synced_at"] = datetime.now(CST).isoformat(timespec="seconds")
        data["meta"] = meta
        with p.open("w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.info("堆场备份已写入: %s（%d 个堆场）", path, len(data.get("yards", [])))
        return True
    except Exception as e:
        logger.error("写入堆场备份失败: %s", e)
        return False


def build_indices(backup: dict) -> tuple[dict[str, float], dict[str, float]]:
    """从备份构建 origin_lat 与 bay_spacing 索引。"""
    origin_lat: dict[str, float] = {}
    bay_spacing: dict[str, float] = {}
    for entry in backup.get("yards", []):
        from src.solver.yard_manager import parse_yard_config_entry

        parsed = parse_yard_config_entry(entry)
        if not parsed:
            continue
        yid = parsed["yard_id"]
        origin_lat[yid] = parsed["origin_lat"]
        bay_spacing[yid] = parsed["bay_spacing"]
    return origin_lat, bay_spacing


def map_snapshot_for_yard(backup: dict, yard_id: str) -> Optional[dict]:
    """取出单堆场的 yard/map 快照，供 CalibrationContextManager 离线加载。"""
    block = (backup.get("yard_data") or {}).get(yard_id)
    if not block:
        return None
    return {
        "yard_id": yard_id,
        "calibration_status": block.get("calibration_status", "none"),
        "calibration_mode": block.get("calibration_mode", "none"),
        "m_matrix": block.get("m_matrix"),
        "source_vehicle_id": block.get("source_vehicle_id"),
        "transform_list": block.get("transform_list") or [],
        "source_transform_list": block.get("source_transform_list") or [],
        "survey_points": block.get("survey_points") or [],
        "has_rtk_map": block.get("has_rtk_map", False),
        "rtk_point_count": block.get("rtk_point_count", 0),
    }
