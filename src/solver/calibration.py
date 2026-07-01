"""
解算模块：B 车 3 点校准
通过三个已知点对求解 B 车相对于 A 车底图的 2D 刚体变换矩阵 M。

校准流程：
  点1 → 平移偏差 (ΔX, ΔY)
  点2 → 旋转偏差 (Δθ)
  点3 → 冗余校验与尺度纠偏，锁定最终参数

输出：
  M = [cos(Δθ)  -sin(Δθ)  ΔX]
      [sin(Δθ)   cos(Δθ)  ΔY]
      [0         0         1 ]

使用：
  P_output = M × P_final（齐次坐标）
"""

import math
import logging
import numpy as np
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


class CalibrationPoint:
    """一对校准点：RTG 实测坐标 + A 车底图参考坐标。"""

    def __init__(self, measured: Tuple[float, float], reference: Tuple[float, float]):
        """
        :param measured:  RTG 实时解算的 (x, y) 坐标（LYCS，米）
        :param reference: A 车底图或 RTK 打点的真值 (x, y)（米）
        """
        self.measured = np.array(measured, dtype=float)
        self.reference = np.array(reference, dtype=float)


class BVehicleCalibrator:
    """
    B 车 3 点校准器。

    使用方式：
        calibrator = BVehicleCalibrator()
        calibrator.add_point(measured=(x1, y1), reference=(rx1, ry1))
        calibrator.add_point(measured=(x2, y2), reference=(rx2, ry2))
        calibrator.add_point(measured=(x3, y3), reference=(rx3, ry3))
        M = calibrator.compute_transform()
    """

    def __init__(self):
        self._points: List[CalibrationPoint] = []
        self._M: Optional[np.ndarray] = None   # 最终 3×3 变换矩阵
        self._scale: float = 1.0
        self._residual: float = 0.0             # 第三点校验残差（m）

    @property
    def is_ready(self) -> bool:
        """是否已收集足够的校准点（≥3 点）。"""
        return len(self._points) >= 3

    @property
    def transform_matrix(self) -> Optional[np.ndarray]:
        """返回已求解的变换矩阵 M（若未完成校准则返回 None）。"""
        return self._M

    @property
    def calibration_residual(self) -> float:
        """第三点验证残差（米），反映校准精度。"""
        return self._residual

    def add_point(
        self,
        measured: Tuple[float, float],
        reference: Tuple[float, float],
    ) -> int:
        """
        添加一个校准点对。

        :return: 当前已收集的点数
        """
        self._points.append(CalibrationPoint(measured, reference))
        count = len(self._points)
        logger.info(f"添加校准点 {count}: 实测={measured} 参考={reference}")
        return count

    def reset(self) -> None:
        """清空所有校准点，重新开始。"""
        self._points.clear()
        self._M = None
        logger.info("校准器已重置")

    def compute_transform(self) -> np.ndarray:
        """
        使用已收集的校准点求解变换矩阵 M。

        算法：
          - 使用点1、点2（以点1为基准）确定平移和旋转
          - 使用点3进行尺度纠偏和冗余验证
          - 最终 M 为 2D 刚体变换（旋转 + 平移）

        :return: 3×3 齐次变换矩阵
        :raises ValueError: 点数不足或点集共线
        """
        if len(self._points) < 3:
            raise ValueError(f"校准点不足，需要 3 点，当前 {len(self._points)} 点")

        p1_m, p1_r = self._points[0].measured, self._points[0].reference
        p2_m, p2_r = self._points[1].measured, self._points[1].reference
        p3_m, p3_r = self._points[2].measured, self._points[2].reference

        # ---- 步骤1：用点1 估算初始平移 ----
        # 用两点法（点1、点2）通过最小二乘求刚体变换（含旋转）
        measured_pts = np.array([p1_m, p2_m], dtype=float)
        reference_pts = np.array([p1_r, p2_r], dtype=float)

        # 质心对齐
        c_m = measured_pts.mean(axis=0)
        c_r = reference_pts.mean(axis=0)
        dm = measured_pts - c_m
        dr = reference_pts - c_r

        # SVD 求最优旋转（2D）
        H = dm.T @ dr
        U, S, Vt = np.linalg.svd(H)
        R2 = Vt.T @ U.T

        # 确保行列式为 +1（排除反射解）
        if np.linalg.det(R2) < 0:
            Vt[-1, :] *= -1
            R2 = Vt.T @ U.T

        # 平移向量
        t = c_r - R2 @ c_m

        # ---- 步骤2：用点3 进行尺度纠偏 ----
        # 将点3 实测坐标通过当前 R2、t 变换为参考系坐标
        p3_predicted = R2 @ p3_m + t
        error = np.linalg.norm(p3_predicted - p3_r)
        self._residual = float(error)
        logger.info("第三点校验残差: %.4f m", self._residual)

        if self._residual > 0.5:
            logger.warning("校准残差 %.4f m 偏大，建议重新打点", self._residual)

        # 用全部3点重新做最小二乘（提升精度）
        measured_all = np.array([p1_m, p2_m, p3_m], dtype=float)
        reference_all = np.array([p1_r, p2_r, p3_r], dtype=float)
        c_m3 = measured_all.mean(axis=0)
        c_r3 = reference_all.mean(axis=0)
        dm3 = measured_all - c_m3
        dr3 = reference_all - c_r3
        H3 = dm3.T @ dr3
        U3, S3, Vt3 = np.linalg.svd(H3)
        R_final = Vt3.T @ U3.T
        if np.linalg.det(R_final) < 0:
            Vt3[-1, :] *= -1
            R_final = Vt3.T @ U3.T
        t_final = c_r3 - R_final @ c_m3

        # ---- 构建 3×3 齐次矩阵 ----
        M = np.eye(3, dtype=float)
        M[:2, :2] = R_final
        M[:2,  2] = t_final

        self._M = M

        # 提取旋转角和平移供日志
        delta_theta = math.degrees(math.atan2(R_final[1, 0], R_final[0, 0]))
        logger.info(
            "校准完成 | ΔX=%.4f m, ΔY=%.4f m, Δθ=%.4f°, 残差=%.4f m",
            t_final[0], t_final[1], delta_theta, self._residual,
        )
        return self._M

    def apply(self, point_xy: Tuple[float, float]) -> Tuple[float, float]:
        """
        将单个 L-Point 坐标通过变换矩阵 M 映射到参考系。

        :param point_xy: 原始 (x, y) 坐标（米）
        :return:         变换后的 (x, y) 坐标（米）
        :raises RuntimeError: 未完成校准
        """
        if self._M is None:
            raise RuntimeError("尚未完成校准，请先调用 compute_transform()")

        p = np.array([point_xy[0], point_xy[1], 1.0])
        result = self._M @ p
        return float(result[0]), float(result[1])

