import paho.mqtt.client as mqtt
import json
import threading
from typing import Callable, Optional
from app.config import settings
from app.utils.logger import get_logger
from app.utils.constants import *

logger = get_logger(__name__)

class MqttClient:
    """MQTT 客户端类"""

    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.on_subscribe = self._on_subscribe

        self.connected = False
        self.message_handler: Optional[Callable] = None
        self._lock = threading.Lock()

    def set_message_handler(self, handler: Callable):
        """设置消息处理器回调"""
        self.message_handler = handler

    def connect(self):
        """连接到 MQTT Broker"""
        try:
            if settings.MQTT_USERNAME and settings.MQTT_PASSWORD:
                self.client.username_pw_set(
                    settings.MQTT_USERNAME,
                    settings.MQTT_PASSWORD
                )

            self.client.connect(
                settings.MQTT_BROKER_HOST,
                settings.MQTT_BROKER_PORT,
                keepalive=60
            )
            self.client.loop_start()
            logger.info(f"连接中: {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
        except Exception as e:
            logger.error(f"MQTT 连接失败: {e}")
            raise

    def disconnect(self):
        """断开连接"""
        self.client.loop_stop()
        self.client.disconnect()
        logger.info("MQTT 已断开连接")

    def subscribe(self, topic: str, qos: int = 0):
        """订阅主题"""
        try:
            self.client.subscribe(topic, qos=qos)
            logger.debug(f"订阅主题: {topic}")
        except Exception as e:
            logger.error(f"订阅失败 {topic}: {e}")

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False):
        """发布消息"""
        try:
            self.client.publish(topic, payload, qos=qos, retain=retain)
        except Exception as e:
            logger.error(f"发布失败 {topic}: {e}")

    def _on_connect(self, client, userdata, flags, rc):
        """连接回调"""
        if rc == 0:
            self.connected = True
            logger.info("MQTT 已连接")

            # 订阅关键主题
            self.subscribe(f"rtg/+/telemetry", qos=settings.MQTT_QOS_TELEMETRY)
            self.subscribe(f"rtg/+/status", qos=settings.MQTT_QOS_CONTROL)
            self.subscribe(f"rtg/discovery", qos=settings.MQTT_QOS_CONTROL)
        else:
            self.connected = False
            logger.error(f"MQTT 连接失败，错误码: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """断开连接回调"""
        self.connected = False
        if rc != 0:
            logger.warning(f"MQTT 意外断开连接，错误码: {rc}")
        else:
            logger.info("MQTT 已断开连接")

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        """订阅确认回调"""
        logger.debug(f"订阅确认，QoS: {granted_qos}")

    def _on_message(self, client, userdata, msg):
        """消息接收回调"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')

            # 解析为 JSON
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = payload

            logger.debug(f"收到消息 {topic}: {data}")

            # 调用消息处理器
            if self.message_handler:
                self.message_handler(topic, data)

        except Exception as e:
            logger.error(f"处理 MQTT 消息失败: {e}")

    def is_connected(self) -> bool:
        """检查连接状态"""
        return self.connected
