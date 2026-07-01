"""B 车 3 点平面刚体变换矩阵 M（与中控 calibration.py 算法一致）"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple


def _svd_2x2(H: List[List[float]]) -> Tuple[List[List[float]], List[List[float]]]:
    """2×2 矩阵 SVD，返回 U, Vt。"""
    a, b = H[0][0], H[0][1]
    c, d = H[1][0], H[1][1]
    # H = U S Vt for 2x2 via eigen of H^T H
    hh00 = a * a + c * c
    hh01 = a * b + c * d
    hh11 = b * b + d * d
    trace = hh00 + hh11
    det_h = hh00 * hh11 - hh01 * hh01
    disc = max(trace * trace / 4.0 - det_h, 0.0)
    root = math.sqrt(disc)
    lam1 = trace / 2.0 + root
    lam2 = trace / 2.0 - root

    def eigenvec(lam: float) -> Tuple[float, float]:
        if abs(hh01) > 1e-12:
            return (lam - hh11, hh01)
        if hh00 >= hh11:
            return (1.0, 0.0)
        return (0.0, 1.0)

    v1x, v1y = eigenvec(lam1)
    n1 = math.hypot(v1x, v1y) or 1.0
    v1x, v1y = v1x / n1, v1y / n1
    v2x, v2y = -v1y, v1x
    Vt = [[v1x, v1y], [v2x, v2y]]

    Hv1x = a * v1x + b * v1y
    Hv1y = c * v1x + d * v1y
    Hv2x = a * v2x + b * v2y
    Hv2y = c * v2x + d * v2y
    s1 = math.hypot(Hv1x, Hv1y)
    s2 = math.hypot(Hv2x, Hv2y)
    u1x, u1y = (Hv1x / s1, Hv1y / s1) if s1 > 1e-12 else (1.0, 0.0)
    u2x, u2y = (Hv2x / s2, Hv2y / s2) if s2 > 1e-12 else (-u1y, u1x)
    U = [[u1x, u2x], [u1y, u2y]]
    return U, Vt


def _mat_mul(A: List[List[float]], B: List[List[float]]) -> List[List[float]]:
    rows, cols, inner = len(A), len(B[0]), len(B)
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            out[i][j] = sum(A[i][k] * B[k][j] for k in range(inner))
    return out


def _transpose(M: List[List[float]]) -> List[List[float]]:
    return [[M[j][i] for j in range(len(M))] for i in range(len(M[0]))]


def _rigid_2d(measured: Sequence[Tuple[float, float]], reference: Sequence[Tuple[float, float]]):
    n = len(measured)
    cx_m = sum(p[0] for p in measured) / n
    cy_m = sum(p[1] for p in measured) / n
    cx_r = sum(p[0] for p in reference) / n
    cy_r = sum(p[1] for p in reference) / n
    dm = [(p[0] - cx_m, p[1] - cy_m) for p in measured]
    dr = [(p[0] - cx_r, p[1] - cy_r) for p in reference]
    H = [[0.0, 0.0], [0.0, 0.0]]
    for (mx, my), (rx, ry) in zip(dm, dr):
        H[0][0] += mx * rx
        H[0][1] += mx * ry
        H[1][0] += my * rx
        H[1][1] += my * ry
    U, Vt = _svd_2x2(H)
    R = _mat_mul(Vt, _transpose(U))
    det = R[0][0] * R[1][1] - R[0][1] * R[1][0]
    if det < 0:
        Vt[1][0] *= -1
        Vt[1][1] *= -1
        R = _mat_mul(Vt, _transpose(U))
    tx = cx_r - (R[0][0] * cx_m + R[0][1] * cy_m)
    ty = cy_r - (R[1][0] * cx_m + R[1][1] * cy_m)
    return R, tx, ty


def compute_transform_matrix(
    measured_xy: Sequence[Tuple[float, float]],
    reference_xy: Sequence[Tuple[float, float]],
) -> Tuple[List[List[float]], float]:
    """
    由 ≥3 对 (measured, reference) 求 3×3 齐次矩阵 M 与第 3 点残差（米）。
    """
    if len(measured_xy) < 3 or len(reference_xy) < 3:
        raise ValueError("至少需要 3 对校准点")

    p1m, p2m, p3m = measured_xy[0], measured_xy[1], measured_xy[2]
    p1r, p2r, p3r = reference_xy[0], reference_xy[1], reference_xy[2]

    R2, tx2, ty2 = _rigid_2d([p1m, p2m], [p1r, p2r])
    pred3x = R2[0][0] * p3m[0] + R2[0][1] * p3m[1] + tx2
    pred3y = R2[1][0] * p3m[0] + R2[1][1] * p3m[1] + ty2
    residual = math.hypot(pred3x - p3r[0], pred3y - p3r[1])

    R, tx, ty = _rigid_2d(measured_xy[:3], reference_xy[:3])
    M = [
        [R[0][0], R[0][1], tx],
        [R[1][0], R[1][1], ty],
        [0.0, 0.0, 1.0],
    ]
    return M, residual
