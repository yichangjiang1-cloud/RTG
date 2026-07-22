"""
解算模块：堆场地图管理器
负责加载/保存 yard_map.json，以及识别 RTG 当前所在堆场。
"""
import json
import os
import logging
import numpy as np
from typing import Optional, Dict, Any
from src.solver.coordinate_transform import YardTransform
logger = logging.getLogger(__name__)

PLC_YARD_ID_MAX_LEN = 8


def parse_yard_config_entry(entry: dict) -> Optional[Dict[str, Any]]:
    """
    解析单条堆场配置，字段与后端 yards 表 / API 一致。
    兼容旧版嵌套 origin + id 写法。
    """
    yard_id = entry.get("yard_id") or entry.get("id")
    if not yard_id:
        return None

    if "origin_lat" in entry:
        origin_lat = float(entry["origin_lat"])
        origin_lon = float(entry["origin_lon"])
        origin_alt = float(entry.get("origin_alt") or 0.0)
    else:
        origin = entry.get("origin") or {}
        origin_lat = float(origin.get("lat", 0.0))
        origin_lon = float(origin.get("lon", 0.0))
        origin_alt = float(origin.get("alt", 0.0))

    heading = entry.get("heading_deg", entry.get("heading", 0.0))
    return {
        "yard_id": yard_id,
        "name": entry.get("name", ""),
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "origin_alt": origin_alt,
        "heading_deg": float(heading),
        "bay_spacing": float(entry.get("bay_spacing", 6.0)),
        "bay_count": int(entry.get("bay_count", entry.get("total_bays", 0))),
    }


class YardManager:
    """
    堆场地图管理器。
    功能：
      1. 从 yard_config.yaml 构建各堆场的 YardTransform 对象
      2. 加载 yard_map.json 中已标定的 B 车变换矩阵
      3. 根据 RTG 当前 WGS84 坐标自动判断所在堆场
    """
    def __init__(self, yard_map_path: str, yard_configs: list):
        self._map_path = yard_map_path
        self._transforms: Dict[str, YardTransform] = {}
        self._yard_meta: Dict[str, Dict[str, Any]] = {}
        self._b_matrices: Dict[str, Any] = {}
        self._current_yard_id: Optional[str] = None
        for yard_cfg in yard_configs:
            parsed = parse_yard_config_entry(yard_cfg)
            if not parsed:
                logger.warning("跳过无效堆场配置项: %s", yard_cfg)
                continue
            yid = parsed["yard_id"]
            if len(yid.encode("ascii", errors="replace")) > PLC_YARD_ID_MAX_LEN:
                logger.warning(
                    "堆场 %s 的 yard_id 超过 %d 字符，PLC 场号将被截断",
                    yid,
                    PLC_YARD_ID_MAX_LEN,
                )
            self._transforms[yid] = YardTransform(
                origin_lat=parsed["origin_lat"],
                origin_lon=parsed["origin_lon"],
                origin_alt=parsed["origin_alt"],
                heading_deg=parsed["heading_deg"],
            )
            self._yard_meta[yid] = parsed
            logger.info("已加载堆场配置: %s (%s)", yid, parsed.get("name", ""))
        self._load_map()

    def reload_yards(self, yard_configs: list) -> None:
        """从 API 或备份 YAML 刷新堆场投影定义。"""
        self._transforms.clear()
        self._yard_meta.clear()
        self._current_yard_id = None
        for yard_cfg in yard_configs:
            parsed = parse_yard_config_entry(yard_cfg)
            if not parsed:
                continue
            yid = parsed["yard_id"]
            if len(yid.encode("ascii", errors="replace")) > PLC_YARD_ID_MAX_LEN:
                logger.warning(
                    "堆场 %s 的 yard_id 超过 %d 字符，PLC 场号将被截断",
                    yid,
                    PLC_YARD_ID_MAX_LEN,
                )
            self._transforms[yid] = YardTransform(
                origin_lat=parsed["origin_lat"],
                origin_lon=parsed["origin_lon"],
                origin_alt=parsed["origin_alt"],
                heading_deg=parsed["heading_deg"],
            )
            self._yard_meta[yid] = parsed
        logger.info("堆场投影已刷新，共 %d 个堆场", len(self._transforms))
    def detect_yard(self, lat: float, lon: float) -> Optional[str]:
        """根据 WGS84 坐标判断所在堆场（取距离最近的堆场原点）。"""
        best_id = None
        best_dist = float("inf")
        for yid, tf in self._transforms.items():
            lx, ly, _ = tf.wgs84_to_lycs(lat, lon, 0.0)
            dist = (lx ** 2 + ly ** 2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_id = yid
        if best_id and best_id != self._current_yard_id:
            logger.info("RTG 切换堆场: %s -> %s (距原点 %.1f m)",
                        self._current_yard_id, best_id, best_dist)
            self._current_yard_id = best_id
        return best_id
    def get_transform(self, yard_id=None) -> Optional[YardTransform]:
        """获取指定堆场（或当前堆场）的 YardTransform。"""
        yid = yard_id or self._current_yard_id
        return self._transforms.get(yid)

    def get_yard_meta(self, yard_id: str) -> Optional[Dict[str, Any]]:
        """堆场元数据（名称、贝位间距等）。"""
        return self._yard_meta.get(yard_id)
    def save_b_matrix(self, yard_id: str, M: np.ndarray) -> None:
        """保存 B 车校准矩阵并持久化到 yard_map.json。"""
        self._b_matrices[yard_id] = M
        self._save_map()
        logger.info(f"B 车变换矩阵已保存: 堆场 {yard_id}")
    def get_b_matrix(self, yard_id: str) -> Optional[np.ndarray]:
        """获取指定堆场的 B 车变换矩阵，未校准返回 None。"""
        return self._b_matrices.get(yard_id)
    def _load_map(self) -> None:
        if not os.path.exists(self._map_path):
            logger.info(f"yard_map.json 不存在，等待标定: {self._map_path}")
            return
        try:
            with open(self._map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for yard_id, entry in data.get("b_matrices", {}).items():
                self._b_matrices[yard_id] = np.array(entry, dtype=float)
            logger.info(f"已加载 yard_map.json，含 {len(self._b_matrices)} 个堆场矩阵")
        except Exception as e:
            logger.error(f"加载 yard_map.json 失败: {e}")
    def _save_map(self) -> None:
        os.makedirs(os.path.dirname(self._map_path) or ".", exist_ok=True)
        data = {"b_matrices": {yid: M.tolist() for yid, M in self._b_matrices.items()}}
        try:
            with open(self._map_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存 yard_map.json 失败: {e}")
