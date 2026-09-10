#!/usr/bin/env python3
"""
RTG 后端服务快速测试脚本
按业务流程顺序：健康检查 → 设备初始化 → 堆场创建 → 打点 → 校准 → 克隆
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
HEADERS = {"Content-Type": "application/json"}


def pretty(resp):
    print(f"  状态码: {resp.status_code}")
    try:
        print(f"  响应: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")
    except Exception:
        print(f"  响应: {resp.text}")
    return resp.status_code == 200


# ==================== 健康检查 ====================

def test_health():
    print("\n=== 1. 系统健康检查 GET /api/health ===")
    return pretty(requests.get(f"{BASE_URL}/api/health", timeout=5))


def test_mqtt_health():
    print("\n=== 2. MQTT 状态 GET /api/health/mqtt ===")
    return pretty(requests.get(f"{BASE_URL}/api/health/mqtt", timeout=5))


def test_db_health():
    print("\n=== 3. 数据库状态 GET /api/health/db ===")
    return pretty(requests.get(f"{BASE_URL}/api/health/db", timeout=5))


def test_websocket_status():
    print("\n=== 4. WebSocket 状态 GET /api/status/websocket ===")
    return pretty(requests.get(f"{BASE_URL}/api/status/websocket", timeout=5))


# ==================== 车辆管理 ====================

def test_vehicle_init():
    print("\n=== 5. 初始化设备 POST /api/vehicle/init ===")
    payload = {
        "temp_id": "RTG-NEW-8b36",
        "new_vehicle_id": "RTG-001",
        "label": "RTG 01号机",
        "params": {
            "vehicle_id": "RTG-001",
            "l_arm": [0.12, 0.08, -8.45],
            "height": 8.45,
            "w_span": 20.0,
            "antennas": {
                "ant1": {"label": "大车左前"},
                "ant2": {"label": "大车右前"},
                "ant3": {"label": "大车左后"},
                "ant4": {"label": "大车右后"},
                "ant5": {"label": "小车A端"},
                "ant6": {"label": "小车B端"}
            }
        }
    }
    return pretty(requests.post(f"{BASE_URL}/api/vehicle/init", json=payload, timeout=5))


def test_vehicle_list():
    print("\n=== 6. 获取车辆列表 GET /api/vehicle/list ===")
    return pretty(requests.get(f"{BASE_URL}/api/vehicle/list", timeout=5))


def test_vehicle_detail():
    print("\n=== 7. 获取车辆详情 GET /api/vehicle/RTG-001 ===")
    return pretty(requests.get(f"{BASE_URL}/api/vehicle/RTG-001", timeout=5))


def test_vehicle_config_save():
    print("\n=== 8. 保存车辆参数 POST /api/vehicle/config ===")
    payload = {
        "vehicle_id": "RTG-001",
        "l_arm": [0.15, 0.10, -8.50],
        "height": 8.50,
        "w_span": 20.5,
        "antennas": {
            "ant1": {"label": "大车左前"},
            "ant2": {"label": "大车右前"},
            "ant3": {"label": "大车左后"},
            "ant4": {"label": "大车右后"},
            "ant5": {"label": "小车A端"},
            "ant6": {"label": "小车B端"}
        }
    }
    return pretty(requests.post(f"{BASE_URL}/api/vehicle/config", json=payload, timeout=5))


def test_vehicle_config_get():
    print("\n=== 9. 获取车辆参数 GET /api/vehicle/config/RTG-001 ===")
    return pretty(requests.get(f"{BASE_URL}/api/vehicle/config/RTG-001", timeout=5))


def test_vehicle_clone():
    print("\n=== 10. 复制车辆配置 POST /api/vehicle/config/clone ===")
    payload = {
        "source_vehicle_id": "RTG-001",
        "new_vehicle_id": "RTG-002"
    }
    return pretty(requests.post(f"{BASE_URL}/api/vehicle/config/clone", json=payload, timeout=5))


# ==================== 堆场管理 ====================

def test_yard_create():
    print("\n=== 11. 创建堆场 POST /api/yard/create ===")
    payload = {
        "yard_id": "yard_01",
        "name": "一号堆场",
        "origin_lat": 22.345678,
        "origin_lon": 113.987654,
        "origin_alt": 5.20,
        "heading_deg": 87.5,
        "bay_spacing": 6.058,
        "bay_count": 20
    }
    return pretty(requests.post(f"{BASE_URL}/api/yard/create", json=payload, timeout=5))


def test_yard_list():
    print("\n=== 12. 获取堆场列表 GET /api/yard/all_origins ===")
    return pretty(requests.get(f"{BASE_URL}/api/yard/all_origins", timeout=5))


def test_yard_map():
    print("\n=== 13. 获取堆场底图 GET /api/yard/map?yard_id=yard_01 ===")
    return pretty(requests.get(f"{BASE_URL}/api/yard/map", params={"yard_id": "yard_01"}, timeout=5))


# ==================== 打点管理 ====================

def test_survey_confirm():
    print("\n=== 16. 确认到达贝位 POST /api/survey/confirm ===")
    payload = {
        "vehicle_id": "RTG-001",
        "yard_id": "yard_01",
        "bay_id": "1",
        "mode": "mapping"
    }
    return pretty(requests.post(f"{BASE_URL}/api/survey/confirm", json=payload, timeout=5))


def test_survey_finalize():
    print("\n=== 17. 完成校准 POST /api/survey/finalize ===")
    payload = {
        "vehicle_id": "RTG-001",
        "yard_id": "yard_01",
        "calibration_data": {
            "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "t": [0.01, -0.02, 0.05]
        }
    }
    return pretty(requests.post(f"{BASE_URL}/api/survey/finalize", json=payload, timeout=5))


def test_survey_history():
    print("\n=== 18. 查询打点历史 GET /api/survey/history?yard_id=yard_01 ===")
    return pretty(requests.get(f"{BASE_URL}/api/survey/history", params={"yard_id": "yard_01"}, timeout=5))


# ==================== 主流程 ====================

def main():
    print("=" * 60)
    print("RTG 后端服务 - 全接口测试")
    print("=" * 60)
    print(f"后端地址: {BASE_URL}")
    print(f"测试时间: {datetime.now().isoformat()}")
    print(f"Swagger UI: {BASE_URL}/docs")

    tests = [
        ("系统健康检查", test_health),
        ("MQTT 状态", test_mqtt_health),
        ("数据库状态", test_db_health),
        ("WebSocket 状态", test_websocket_status),
        ("初始化设备 RTG-001", test_vehicle_init),
        ("获取车辆列表", test_vehicle_list),
        ("获取车辆详情", test_vehicle_detail),
        ("保存车辆参数", test_vehicle_config_save),
        ("获取车辆参数", test_vehicle_config_get),
        ("复制配置 → RTG-002", test_vehicle_clone),
        ("创建堆场 yard_01", test_yard_create),
        ("获取堆场列表", test_yard_list),
        ("获取堆场底图", test_yard_map),
        ("确认到达贝位", test_survey_confirm),
        ("完成校准", test_survey_finalize),
        ("查询打点历史", test_survey_history),
    ]

    results = []
    for name, test_func in tests:
        try:
            result = test_func()
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            result = False
        results.append((name, "✓" if result else "✗"))
        time.sleep(0.3)

    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    for name, s in results:
        print(f"  {s} {name}")

    passed = sum(1 for _, s in results if s == "✓")
    total = len(results)
    print(f"\n  通过: {passed}/{total}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n测试已中断")
    except Exception as e:
        print(f"\n\n❌ 测试异常: {e}")
