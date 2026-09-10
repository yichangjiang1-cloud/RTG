"""
通信模块：MQTT 客户端
负责与后端服务的 MQTT Broker 建立连接，实现：
  1. 实时遥测数据推送（telemetry）
  2. 接收控制指令（control）
  3. 上报设备状态（status）
  4. 支持设备身份管理（初始化/注册）
"""

import json
import threading
import time
from typing import Optional, Callable, Dict, Any
import paho.mqtt.client as mqtt

from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)


class MqttClient:
    """
    MQTT 客户端封装，支持多 Topic 订阅/发布。
    
    Topic 设计：
      - rtg/{vehicle_id}/telemetry   中控机→后端：实时定位数据（10Hz）
      - rtg/{vehicle_id}/control     后端→中控机：控制指令（打点、配置更新）
      - rtg/{vehicle_id}/status      中控机→后端：健康度、场地切换等状态
      - rtg/discovery                新设备广播：设备注册请求
    """

    def __init__(
        self,
        broker_host: str,
        broker_port: int = 1883,
        vehicle_id: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        keepalive: int = 60,
    ):
        """
        :param broker_host:  MQTT Broker 地址
        :param broker_port:  MQTT Broker 端口
        :param vehicle_id:   车辆 ID（如为临时ID则通过discovery注册）
        :param username:     MQTT 认证用户名
        :param password:     MQTT 认证密码
        :param keepalive:    心跳间隔（秒）
        """
        # Windows + Docker 下 localhost 会解析成 ::1，连到 wslrelay 而非 0.0.0.0 映射
        if broker_host in ("localhost", "::1"):
            broker_host = "127.0.0.1"
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._vehicle_id  = vehicle_id or self._generate_temp_id()
        self._keepalive   = keepalive

        # MQTT 客户端（paho 2.x 首参是 CallbackAPIVersion，须显式 VERSION1）
        client_id = f"rtg_{self._vehicle_id}_{int(time.time())}"
        cb_api = getattr(mqtt, "CallbackAPIVersion", None)
        if cb_api is not None:
            self._client = mqtt.Client(
                cb_api.VERSION1,
                client_id=client_id,
                clean_session=True,
            )
        else:
            self._client = mqtt.Client(
                client_id=client_id,
                clean_session=True,
            )
        # YAML 未加引号时 password: 123456 会被解析成 int，paho 2.x 会对密码做 len()
        user = str(username).strip() if username is not None else ""
        pwd = "" if password is None else str(password)
        if user:
            self._client.username_pw_set(user, pwd)

        # 回调注册
        self._control_callback: Optional[Callable[[dict], None]] = None
        self._connected_callback: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()

        # 连接状态
        self._connected = False
        self._stop_event = threading.Event()

        # 设置 MQTT 回调
        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        logger.info(f"MQTT Client 初始化完成，Vehicle ID: {self._vehicle_id}")

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动 MQTT 客户端，连接 Broker。"""
        logger.info(f"启动 MQTT 客户端，目标 Broker: {self._broker_host}:{self._broker_port}")
        try:
            self._client.connect(
                self._broker_host, 
                self._broker_port, 
                self._keepalive
            )
            self._client.loop_start()
            logger.info("MQTT 后台循环已启动，等待连接回调...")
        except Exception as e:
            logger.error(f"MQTT 连接失败: {e}")
            try:
                self._client.disconnect()
            except Exception:
                pass
            raise

    def stop(self) -> None:
        """停止 MQTT 客户端。"""
        logger.info("正在停止 MQTT 客户端...")
        self._stop_event.set()
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("✓ MQTT Client 已停止")

    def is_connected(self) -> bool:
        """返回连接状态。"""
        return self._connected

    # ------------------------------------------------------------------
    # 身份管理
    # ------------------------------------------------------------------
    def update_vehicle_id(self, new_id: str) -> None:
        """
        更新车辆 ID（用于设备初始化后更新正式 ID）。
        更新后会重新订阅新 ID 的 Topic。
        """
        old_id = self._vehicle_id
        self._vehicle_id = new_id
        logger.info(f"Vehicle ID 更新: {old_id} -> {new_id}")
        
        # 取消旧订阅，订阅新 Topic
        if self._connected:
            self._client.unsubscribe(f"rtg/{old_id}/control")
            self._client.subscribe(f"rtg/{new_id}/control", qos=1)

    def get_vehicle_id(self) -> str:
        """返回当前 Vehicle ID。"""
        return self._vehicle_id

    @staticmethod
    def _generate_temp_id() -> str:
        """生成临时 ID（基于 MAC 地址）。"""
        import uuid
        mac = uuid.getnode()
        mac_str = f"{mac:012x}"[-4:]  # 取后4位
        return f"RTG-NEW-{mac_str}"

    # ------------------------------------------------------------------
    # 发布接口
    # ------------------------------------------------------------------
    def publish_telemetry(self, data: Dict[str, Any]) -> None:
        """
        发布实时遥测数据（10Hz 高频）。
        
        :param data: 定位结果字典，包含 gantry/trolley 数据
        """
        topic = f"rtg/{self._vehicle_id}/telemetry"
        payload = json.dumps(data, ensure_ascii=False)
        try:
            result = self._client.publish(topic, payload, qos=0)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(f"遥测数据发布失败: rc={result.rc}, topic={topic}")
            else:
                logger.debug(f"遥测数据已发布到 {topic}, 数据大小: {len(payload)} 字节")
        except Exception as e:
            logger.exception(f"发布遥测数据异常: {e}")

    def publish_status(self, status_data: Dict[str, Any]) -> None:
        """
        发布设备状态（低频，事件驱动）。
        
        :param status_data: 状态字典，如堆场切换、健康度等
        """
        topic = f"rtg/{self._vehicle_id}/status"
        payload = json.dumps(status_data, ensure_ascii=False)
        event_type = status_data.get("event", "unknown")
        try:
            self._client.publish(topic, payload, qos=1)
            logger.info(f"✓ 设备状态已发布: 事件={event_type}, topic={topic}")
        except Exception as e:
            logger.exception(f"发布设备状态异常: {e}")

    def publish_discovery(self, device_info: Dict[str, Any]) -> None:
        """
        发布设备发现消息（未初始化设备广播）。
        
        :param device_info: 设备信息，包含 MAC、临时 ID 等
        """
        topic = "rtg/discovery"
        payload = json.dumps(device_info, ensure_ascii=False)
        try:
            self._client.publish(topic, payload, qos=1)
            logger.info(f"✓ 设备发现消息已广播: temp_id={device_info.get('temp_id')}, mac={device_info.get('mac')}")
        except Exception as e:
            logger.exception(f"发布发现消息异常: {e}")

    # ------------------------------------------------------------------
    # 订阅接口
    # ------------------------------------------------------------------
    def set_control_callback(self, callback: Callable[[dict], None]) -> None:
        """
        设置控制指令回调函数。
        
        :param callback: 回调函数，接收解析后的 dict
        """
        with self._lock:
            self._control_callback = callback

    def set_on_connected_callback(self, callback: Callable[[], None]) -> None:
        """MQTT 连接成功（含重连）后的回调，用于上报上线 / discovery。"""
        with self._lock:
            self._connected_callback = callback

    # ------------------------------------------------------------------
    # MQTT 事件回调
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        """MQTT 连接回调。"""
        if rc == 0:
            self._connected = True
            logger.info(f"✓ MQTT 已连接成功: {self._broker_host}:{self._broker_port}")
            
            # 订阅控制指令 Topic
            control_topic = f"rtg/{self._vehicle_id}/control"
            client.subscribe(control_topic, qos=1)
            logger.info(f"✓ 已订阅控制 Topic: {control_topic}")

            with self._lock:
                cb = self._connected_callback
            if cb:
                try:
                    cb()
                except Exception as e:
                    logger.error(f"✗ MQTT 连接回调执行失败: {e}")
        else:
            self._connected = False
            logger.error(f"✗ MQTT 连接失败: rc={rc}, 原因: {self._get_rc_meaning(rc)}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT 断连回调。"""
        self._connected = False
        if rc != 0:
            logger.warning(f"⚠ MQTT 意外断开: rc={rc}, 将自动重连...")
        else:
            logger.info("MQTT 正常断开")

    def _on_message(self, client, userdata, msg):
        """MQTT 消息回调（处理控制指令）。"""
        try:
            # 解析控制指令
            payload = json.loads(msg.payload.decode("utf-8"))
            cmd_type = payload.get("cmd", "unknown")
            logger.info(f"← 收到 MQTT 消息: topic={msg.topic}, cmd={cmd_type}")
            logger.debug(f"消息内容: {payload}")
            
            # 调用控制回调
            with self._lock:
                if self._control_callback:
                    self._control_callback(payload)
                else:
                    logger.warning(f"⚠ 控制指令无处理器: cmd={cmd_type}")
        except json.JSONDecodeError as e:
            logger.error(f"✗ MQTT 消息解析失败: {e}, payload={msg.payload[:100]}")
        except Exception as e:
            logger.exception(f"✗ 处理 MQTT 消息异常: {e}")
    
    @staticmethod
    def _get_rc_meaning(rc: int) -> str:
        """获取 MQTT 返回码含义"""
        meanings = {
            0: "成功",
            1: "协议版本不正确",
            2: "客户端 ID 无效",
            3: "服务器不可用",
            4: "用户名或密码错误",
            5: "未授权",
        }
        return meanings.get(rc, f"未知错误码: {rc}")


