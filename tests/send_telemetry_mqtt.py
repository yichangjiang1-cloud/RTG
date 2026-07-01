#!/usr/bin/env python3
"""
向本机 MQTT Broker 持续发布 RTG 遥测（5 条/秒），按 Ctrl+C 手动停止。

Topic: rtg/RTG-001/telemetry

用法:
  pip install paho-mqtt
  python tests/send_telemetry_mqtt.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("请先安装: pip install paho-mqtt", file=sys.stderr)
    sys.exit(1)

TOPIC = "rtg/RTG-001/telemetry"
TZ_CN = timezone(timedelta(hours=8))

BASE_PAYLOAD = {
    "gps_coord": {"lat": 30.123456, "lon": 120.654321, "alt": 5.2},
    "local_coord": {"x": 12.5, "y": 3.8, "z": 5.2},
    "heading": 87.3,
    "roll": 0.12,
    "pitch": -0.05,
    "speed": 0.0,
    "status": "fix",
    "receivers": [
        {"id": "ant1", "lat": 30.123456, "lon": 120.654321, "alt": 5.2, "fix_type": "fix"},
        {"id": "ant2", "lat": 30.123460, "lon": 120.654325, "alt": 5.2, "fix_type": "fix"},
    ],
}


def make_payload() -> dict:
    payload = dict(BASE_PAYLOAD)
    payload["timestamp"] = datetime.now(TZ_CN).isoformat(timespec="seconds")
    return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="本机 MQTT 遥测持续发送，Ctrl+C 停止")
    p.add_argument("--host", default="127.0.0.1", help="本机 MQTT Broker（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=1883, help="MQTT 端口")
    p.add_argument("--topic", default=TOPIC, help="发布主题")
    p.add_argument("--rate", type=float, default=5.0, help="发送频率 (条/秒)")
    p.add_argument("--username", default=None)
    p.add_argument("--password", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    interval = 1.0 / args.rate if args.rate > 0 else 0.2

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    if args.username:
        client.username_pw_set(args.username, args.password or "")

    print(f"连接本机 Broker {args.host}:{args.port} ...")
    try:
        client.connect(args.host, args.port, keepalive=60)
    except Exception as e:
        print(f"连接失败: {e}", file=sys.stderr)
        print("请确认本机已启动 Mosquitto（或其它 MQTT 服务）并监听 1883", file=sys.stderr)
        sys.exit(1)

    client.loop_start()
    time.sleep(0.3)

    sent = 0
    print(f"持续发布 → {args.topic}，{args.rate} 条/秒，按 Ctrl+C 停止\n")

    try:
        while True:
            t0 = time.perf_counter()
            payload = make_payload()
            body = json.dumps(payload, ensure_ascii=False)
            result = client.publish(args.topic, body, qos=0)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                print(f"发布失败 rc={result.rc}", file=sys.stderr)

            sent += 1
            if sent % int(max(args.rate, 1)) == 0:
                print(f"[{sent}] {payload['timestamp']}")

            elapsed = time.perf_counter() - t0
            sleep_s = interval - elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)
    except KeyboardInterrupt:
        print("\n手动停止")
    finally:
        client.loop_stop()
        client.disconnect()
        print(f"共发送 {sent} 条")


if __name__ == "__main__":
    main()
