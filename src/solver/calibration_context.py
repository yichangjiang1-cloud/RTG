"""
场地校准上下文：从后端 API 加载 M 矩阵、参考车全场表，供 solver 作业套用。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

import numpy as np

from src.solver.bay_residual import BayAnchor, build_anchors_from_transform_list

if TYPE_CHECKING:
    from src.bridge.api_client import ApiClient

logger = logging.getLogger(__name__)


@dataclass
class YardCalibrationState:
    yard_id: str
    vehicle_id: str
    mode: str = "none"  # full | verify | skip | none
    status: str = "none"
    source_vehicle_id: Optional[str] = None
    matrix: Optional[np.ndarray] = None
    calibration_residual: Optional[float] = None
    own_anchors: List[BayAnchor] = field(default_factory=list)
    source_anchors: List[BayAnchor] = field(default_factory=list)
    survey_points: List[dict] = field(default_factory=list)
    bay_spacing: float = 6.0
    ref_lat_deg: float = 0.0


class CalibrationContextManager:
    """按堆场缓存校准数据，支持从后端刷新。"""

    def __init__(self, bay_spacing_by_yard: Optional[Dict[str, float]] = None):
        self._states: Dict[str, YardCalibrationState] = {}
        self._bay_spacing = bay_spacing_by_yard or {}

    def get(self, yard_id: str) -> Optional[YardCalibrationState]:
        return self._states.get(yard_id)

    def update_bay_spacing(self, spacing_by_yard: Dict[str, float]) -> None:
        """刷新各堆场贝间距（堆场同步后调用）。"""
        self._bay_spacing.update(spacing_by_yard)

    def load_yard(
        self,
        api: "ApiClient",
        yard_id: str,
        vehicle_id: str,
        ref_lat_deg: float = 0.0,
    ) -> Optional[YardCalibrationState]:
        """从 GET /api/yard/map 拉取并解析校准上下文。"""
        raw = api.get_yard_map(yard_id, vehicle_id)
        if not raw:
            logger.warning("无法加载堆场校准: yard=%s vehicle=%s", yard_id, vehicle_id)
            return None
        source_vid = raw.get("source_vehicle_id")
        if not source_vid:
            m_payload = raw.get("m_matrix") or {}
            if isinstance(m_payload, dict):
                source_vid = m_payload.get("source_vehicle_id")
        source_list = None
        if source_vid and source_vid != vehicle_id:
            source_list = api.get_transform_list(source_vid, yard_id)
        return self.load_yard_from_snapshot(
            yard_id, vehicle_id, ref_lat_deg, raw, source_transform_list=source_list
        )

    def load_yard_from_snapshot(
        self,
        yard_id: str,
        vehicle_id: str,
        ref_lat_deg: float,
        raw: dict,
        source_transform_list: Optional[List[dict]] = None,
    ) -> Optional[YardCalibrationState]:
        """从 API 或本地 yard_config.yaml 备份快照加载校准上下文。"""
        if not raw:
            return None

        mode = raw.get("calibration_mode") or "none"
        status = raw.get("calibration_status") or "none"
        m_payload = raw.get("m_matrix") or {}
        if isinstance(m_payload, dict) and m_payload.get("mode"):
            mode = m_payload.get("mode", mode)

        source_vid = raw.get("source_vehicle_id")
        if not source_vid and isinstance(m_payload, dict):
            source_vid = m_payload.get("source_vehicle_id")
        matrix = _parse_matrix(
            m_payload.get("matrix") if isinstance(m_payload, dict) else m_payload
        )
        residual = m_payload.get("calibration_residual") if isinstance(m_payload, dict) else None

        spacing = self._bay_spacing.get(yard_id, 6.0)
        own_list = raw.get("transform_list") or []
        own_anchors = build_anchors_from_transform_list(
            own_list, ref_lat_deg, spacing
        )

        source_anchors: List[BayAnchor] = []
        src_raw = source_transform_list
        if src_raw is None:
            src_raw = raw.get("source_transform_list")

        if mode == "verify" and source_vid and src_raw:
            source_anchors = build_anchors_from_transform_list(
                src_raw, ref_lat_deg, spacing
            )
        elif mode == "full" and status == "completed":
            source_anchors = own_anchors
        elif src_raw and not source_anchors:
            source_anchors = build_anchors_from_transform_list(
                src_raw, ref_lat_deg, spacing
            )

        state = YardCalibrationState(
            yard_id=yard_id,
            vehicle_id=vehicle_id,
            mode=mode,
            status=status,
            source_vehicle_id=source_vid,
            matrix=matrix,
            calibration_residual=residual,
            own_anchors=own_anchors,
            source_anchors=source_anchors,
            survey_points=[
                p for p in (raw.get("survey_points") or [])
                if p.get("bay_no") is not None
            ],
            bay_spacing=spacing,
            ref_lat_deg=ref_lat_deg,
        )
        self._states[yard_id] = state

        if matrix is not None:
            logger.info(
                "校准已加载 yard=%s mode=%s source=%s residual=%s anchors=%d/%d",
                yard_id,
                mode,
                source_vid,
                residual,
                len(own_anchors),
                len(source_anchors),
            )
        else:
            logger.info(
                "校准上下文 yard=%s mode=%s status=%s（无 M 矩阵）",
                yard_id,
                mode,
                status,
            )
        return state

    def apply_b_matrix(self, yard_id: str, M: np.ndarray) -> None:
        """本地 3 点校准完成后写入（兼容 yard_map.json）。"""
        state = self._states.get(yard_id)
        if state is None:
            state = YardCalibrationState(yard_id=yard_id, vehicle_id="")
            self._states[yard_id] = state
        state.matrix = M
        state.mode = state.mode if state.mode != "none" else "verify"


def _parse_matrix(raw) -> Optional[np.ndarray]:
    if raw is None:
        return None
    if isinstance(raw, list) and len(raw) == 3:
        try:
            return np.array(raw, dtype=float)
        except (TypeError, ValueError):
            return None
    return None
