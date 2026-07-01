"""
RTG 高精度自动定位系统 - 主程序入口
启动所有功能节点（driver / fusion / solver / bridge / mqtt），监听系统信号。
"""

import sys
import signal
import time
from pathlib import Path

# 添加项目根目录到模块搜索路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.utils.logger import setup_logging, EnhancedLogger
from src.utils.config_loader import init_config, get_config
from src.utils.device_manager import DeviceManager
from src.driver.driver_node import DriverNode
from src.fusion.fusion_node import FusionNode
from src.solver.solver_node import SolverNode
from src.solver.yard_manager import YardManager
from src.solver.calibration_context import CalibrationContextManager
from src.utils.yard_backup import (
    DEFAULT_BACKUP_PATH,
    fetch_from_api,
    load_backup,
    save_backup,
    build_indices,
    map_snapshot_for_yard,
)
from src.utils.vehicle_params_backup import (
    DEFAULT_VEHICLE_PARAMS_PATH,
    persist_vehicle_params,
)
from src.bridge.bridge_node import BridgeNode
from src.bridge.mqtt_client import MqttClient
from src.bridge.api_client import ApiClient
from src.bridge.telemetry import position_result_to_realtime
from src.core.event_bus import get_bus, EventBus

logger = EnhancedLogger.get_logger("rtg.main")


class RTGSystem:
    """
    RTG 定位系统主控类。
    负责初始化配置、各功能节点的生命周期管理、MQTT通信、后端API对接。
    """

    def __init__(self):
        # 加载配置
        config_files = [
            "config/system.yaml",
            "config/vehicle_params.yaml",
            "config/yard_config.yaml",
        ]
        init_config(*config_files)
        cfg = get_config()

        # 初始化日志
        setup_logging(
            level=cfg.get("logging.level", "INFO"),
            log_file=cfg.get("logging.file", "logs/rtg.log"),
            max_bytes=cfg.get("logging.max_bytes", 10485760),
            backup_count=cfg.get("logging.backup_count", 5),
        )
        logger.info("=" * 60)
        logger.info("RTG 高精度自动定位系统启动")
        logger.info("=" * 60)

        # 设备管理器：加载/生成 vehicle_id
        self._device_mgr = DeviceManager()
        self._vehicle_id = self._device_mgr.load_vehicle_id()
        logger.info(f"当前 Vehicle ID: {self._vehicle_id}")

        # 后端 API 客户端
        backend_url = cfg.get("backend.api_base_url", "http://localhost:8000")
        self._api_client = ApiClient(backend_url)
        
        # 启动时健康检查 + 身份对账（本地正式 ID 须与后端 vehicles 表一致）
        if self._api_client.health_check():
            logger.info(f"✓ 后端服务连接正常: {backend_url}")
            self._vehicle_id = self._device_mgr.reconcile_with_backend(self._api_client)
            logger.info(f"身份对账后 Vehicle ID: {self._vehicle_id}")
        else:
            logger.warning(f"⚠ 后端服务暂时不可达: {backend_url}，跳过身份对账")

        # MQTT 客户端
        mqtt_host = cfg.get("mqtt.broker_host", "localhost")
        mqtt_port = cfg.get("mqtt.broker_port", 1883)
        self._mqtt_client = MqttClient(
            broker_host=mqtt_host,
            broker_port=mqtt_port,
            vehicle_id=self._vehicle_id,
            username=cfg.get("mqtt.username"),
            password=cfg.get("mqtt.password"),
        )
        # 设置 MQTT 回调
        self._mqtt_client.set_control_callback(self._on_mqtt_control)
        self._mqtt_client.set_on_connected_callback(self._on_mqtt_connected)
        self._boot_time = time.time()

        # 堆场：优先 API，失败则读 yard_config.yaml 备份
        self._vehicle_params_path = DEFAULT_VEHICLE_PARAMS_PATH
        self._yard_backup_path = cfg.get(
            "yard_backup.path", DEFAULT_BACKUP_PATH
        )
        yard_map_path = cfg.get("yard_map.path", "data/yard_map.json")
        self._yard_backup = self._bootstrap_yard_backup()
        yard_configs = self._yard_backup.get("yards", [])
        n_yards = len(yard_configs)
        if n_yards == 0:
            logger.warning(
                "⚠ 未加载任何堆场投影（yards 为空），GNSS 解算与前端遥测将不可用，"
                "请同步 API 或在 config/yard_config.yaml 中配置 yards"
            )
        else:
            logger.info(f"✓ 已加载 {n_yards} 个堆场投影")
        self._yard_manager = YardManager(yard_map_path, yard_configs)
        self._yard_origin_lat, bay_spacing_by_yard = build_indices(self._yard_backup)
        self._calibration_ctx = CalibrationContextManager(bay_spacing_by_yard)
        self._apply_calibration_snapshots(self._yard_backup)

        # 事件总线
        self._bus = get_bus()

        # 初始化各功能节点
        self._driver_node = DriverNode()
        self._fusion_node = FusionNode()
        self._solver_node = SolverNode(
            yard_manager=self._yard_manager,
            calibration_ctx=self._calibration_ctx,
        )
        self._bridge_node = BridgeNode()

        # 订阅定位结果，用于 MQTT 推送
        self._bus.subscribe(EventBus.TOPIC_POSITION_RESULT, self._on_position_result)

        # 系统运行标志
        self._running = False
        self._current_yard_id = None

        # 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def start(self) -> None:
        """启动所有节点。"""
        logger.info("=" * 60)
        logger.info("开始启动各功能节点...")
        logger.info("=" * 60)
        try:
            # 启动 MQTT（容错：失败时继续运行）
            logger.info("→ 启动 MQTT 客户端...")
            try:
                self._mqtt_client.start()
                time.sleep(1)  # 等待 MQTT 连接
                logger.info("✓ MQTT 客户端启动完成")
            except Exception as mqtt_err:
                logger.warning(f"⚠ MQTT 启动失败，将在离线模式运行: {mqtt_err}")
                # 继续运行，不中断系统启动

            # 配置对齐：从云端拉取最新配置与校准底图
            if self._device_mgr.is_initialized():
                logger.info("→ 正式设备，开始同步云端配置...")
                self._sync_config_from_cloud()
                self._sync_yard_data_from_cloud()

            # 启动驱动节点
            logger.info("→ 启动驱动节点 (DriverNode)...")
            self._driver_node.start()
            logger.info("✓ DriverNode 启动完成")
            
            # 启动通信节点
            logger.info("→ 启动通信节点 (BridgeNode)...")
            self._bridge_node.start()
            logger.info("✓ BridgeNode 启动完成")
            
            self._running = True
            logger.info("=" * 60)
            logger.info("✓ 系统启动完成，所有节点运行中")
            logger.info(f"✓ Vehicle ID: {self._vehicle_id}")
            logger.info("按 Ctrl+C 停止系统")
            logger.info("=" * 60)
        except Exception as exc:
            logger.exception(f"✗ 启动失败: {exc}")
            self.stop()
            sys.exit(1)

    def stop(self) -> None:
        """停止所有节点。"""
        if not self._running:
            return
        logger.info("系统停止中...")
        self._running = False
        try:
            self._bridge_node.stop()
            self._driver_node.stop()
            self._mqtt_client.stop()
            logger.info("所有节点已停止")
        except Exception as exc:
            logger.exception(f"停止过程出错: {exc}")

    def run_forever(self) -> None:
        """主循环（阻塞直到收到停止信号）。"""
        import time
        try:
            while self._running:
                time.sleep(1.0)
                # 定期输出统计信息（可选）
                if int(time.time()) % 30 == 0:
                    self._log_stats()
        except KeyboardInterrupt:
            logger.info("收到键盘中断")
        finally:
            self.stop()

    def _signal_handler(self, signum, frame):
        """信号处理器（SIGINT / SIGTERM）。"""
        logger.info(f"收到系统信号 {signum}，准备退出")
        self._running = False

    def _log_stats(self) -> None:
        """输出系统运行统计。"""
        bridge_stats = self._bridge_node.get_stats()
        plc_connected = self._bridge_node.is_plc_connected
        mqtt_connected = self._mqtt_client.is_connected()
        logger.info(
            f"系统状态 | PLC: {'✓' if plc_connected else '✗'} | "
            f"MQTT: {'✓' if mqtt_connected else '✗'} | "
            f"发送帧: {bridge_stats.get('sent', 0)} | "
            f"丢弃: {bridge_stats.get('dropped', 0)} | "
            f"错误: {bridge_stats.get('errors', 0)}"
        )

    # ------------------------------------------------------------------
    # MQTT 相关回调
    # ------------------------------------------------------------------
    def _on_mqtt_control(self, cmd: dict) -> None:
        """
        处理 MQTT 控制指令回调。
        
        支持的指令：
          - CMD_INIT_IDENTITY:  设备初始化（临时ID→正式ID）
          - RECORD_POINT:       建图/校准打点
          - UPDATE_CONFIG:      更新车辆配置
          - START_SVD:          执行校准计算
        """
        cmd_type = cmd.get("cmd")
        logger.info(f"← 收到 MQTT 控制指令: cmd={cmd_type}")

        try:
            if cmd_type == "CMD_INIT_IDENTITY":
                self._handle_init_identity(cmd)
            elif cmd_type == "RECORD_POINT":
                self._handle_record_point(cmd)
            elif cmd_type == "UPDATE_CONFIG":
                self._handle_update_config(cmd)
            elif cmd_type == "START_SVD":
                self._handle_start_svd(cmd)
            elif cmd_type == "CALIBRATION_FINALIZED":
                self._handle_calibration_finalized(cmd)
            else:
                logger.warning(f"⚠ 未知控制指令: {cmd_type}")
        except Exception as e:
            logger.exception(f"✗ 处理控制指令异常: {e}")

    def _on_position_result(self, result) -> None:
        """
        订阅定位结果，推送到 MQTT（实时遥测）。
        
        :param result: PositionResult 对象
        """
        if not self._mqtt_client.is_connected():
            logger.debug("MQTT 未连接，跳过定位结果推送")
            return

        telemetry = position_result_to_realtime(result)
        self._mqtt_client.publish_telemetry(telemetry)

    # ------------------------------------------------------------------
    # 控制指令处理器
    # ------------------------------------------------------------------
    def _handle_init_identity(self, cmd: dict) -> None:
        """处理设备初始化指令（临时ID→正式ID）。"""
        new_vehicle_id = cmd.get("data", {}).get("new_vehicle_id")
        if not new_vehicle_id:
            logger.error("✗ 初始化指令缺少 new_vehicle_id")
            return

        logger.info(f"→ 处理设备初始化指令: new_vehicle_id={new_vehicle_id}")

        # 保存到本地配置
        if self._device_mgr.save_vehicle_id(new_vehicle_id):
            self._apply_identity_initialized(new_vehicle_id)
        else:
            logger.error("✗ 设备初始化失败")

    def _apply_identity_initialized(self, new_vehicle_id: str) -> None:
        """设备绑定正式 ID 后的热更新：MQTT 主题、云端配置、堆场校准，无需重启。"""
        self._mqtt_client.update_vehicle_id(new_vehicle_id)
        self._vehicle_id = new_vehicle_id

        self._mqtt_client.publish_status({
            "event": "identity_initialized",
            "vehicle_id": new_vehicle_id,
            "timestamp": time.time(),
        })

        logger.info(f"→ 正式设备 {new_vehicle_id}，热更新云端配置与堆场数据...")
        self._sync_config_from_cloud()
        self._sync_yard_data_from_cloud()
        self._publish_online_status()
        logger.info(f"✓ 设备初始化完成: {new_vehicle_id}（已热更新，无需重启）")

    def _handle_record_point(self, cmd: dict) -> None:
        """处理打点指令（建图/校准）。"""
        data = cmd.get("data", {})
        truth = data.get("truth")  # [x, y, z]
        bay_id = data.get("bay_id")

        if not truth or not bay_id:
            logger.error("✗ 打点指令参数不完整")
            return

        logger.info(f"→ 收到打点指令: 贝位={bay_id}, 真值={truth}")
        # TODO: 触发 solver_node 的校准点采集

    def _handle_update_config(self, cmd: dict) -> None:
        """处理配置更新指令。"""
        config = cmd.get("data", {})
        logger.info(f"→ 收到配置更新指令: 配置项数={len(config)}")
        self._apply_vehicle_config(config)

    def _apply_vehicle_config(self, config: dict) -> None:
        """应用车辆参数到解算器，并回写 vehicle_params.yaml 本地备份。"""
        self._solver_node.apply_vehicle_config(config)
        persist_vehicle_params(config, self._vehicle_params_path)

    def _handle_start_svd(self, cmd: dict) -> None:
        """处理启动 SVD 校准计算指令。"""
        logger.info("→ 收到 SVD 校准指令，刷新云端校准数据")
        self._sync_yard_data_from_cloud()

    def _handle_calibration_finalized(self, cmd: dict) -> None:
        """校准 finalize 后：拉取 API 全量堆场数据，写入 yard_config.yaml 并热更新解算器。"""
        data = cmd.get("data", {})
        yard_id = data.get("yard_id", "")
        logger.info(f"→ 收到校准完成通知: yard_id={yard_id}，同步堆场本地备份...")
        self._sync_yard_data_from_cloud()

    # ------------------------------------------------------------------
    # 堆场数据：API 真源 + yard_config.yaml 备份
    # ------------------------------------------------------------------
    def _bootstrap_yard_backup(self) -> dict:
        """启动时拉取 API 备份；失败则读本地 YAML。"""
        backup = None
        if self._api_client.health_check():
            backup = fetch_from_api(self._api_client, self._vehicle_id)
            if backup and backup.get("yards"):
                save_backup(backup, self._yard_backup_path)
                logger.info("✓ 堆场数据已从 API 同步并写入备份")
        if not backup or not backup.get("yards"):
            backup = load_backup(self._yard_backup_path)
            meta = backup.get("meta") or {}
            logger.warning(
                f"⚠ 使用本地堆场备份: {self._yard_backup_path}"
                f"（source={meta.get('source', 'local')}, "
                f"synced_at={meta.get('synced_at', 'unknown')}）"
            )
        return backup

    def _apply_calibration_snapshots(self, backup: dict) -> None:
        """将备份中的校准快照载入 CalibrationContext（离线或启动兜底）。"""
        if not self._vehicle_id:
            return
        for yard_id in (backup.get("yard_data") or {}):
            snapshot = map_snapshot_for_yard(backup, yard_id)
            if not snapshot:
                continue
            ref_lat = self._yard_origin_lat.get(yard_id, 0.0)
            state = self._calibration_ctx.load_yard_from_snapshot(
                yard_id,
                self._vehicle_id,
                ref_lat,
                snapshot,
                source_transform_list=snapshot.get("source_transform_list"),
            )
            if state and state.matrix is not None:
                self._yard_manager.save_b_matrix(yard_id, state.matrix)

    def _sync_yard_data_from_cloud(self) -> None:
        """同步堆场元数据、RTK 贝点、车辆校准，并更新本地备份。"""
        logger.info("→ 正在从云端同步堆场与校准数据...")
        try:
            backup = fetch_from_api(self._api_client, self._vehicle_id)
            if backup and backup.get("yards"):
                save_backup(backup, self._yard_backup_path)
                self._yard_backup = backup
                self._yard_manager.reload_yards(backup["yards"])
                self._yard_origin_lat, spacing = build_indices(backup)
                self._calibration_ctx.update_bay_spacing(spacing)

                synced = 0
                for yard_id in [y.get("yard_id") for y in backup["yards"] if y.get("yard_id")]:
                    ref_lat = self._yard_origin_lat.get(yard_id, 0.0)
                    self._solver_node.reload_calibration(
                        yard_id,
                        self._vehicle_id,
                        ref_lat_deg=ref_lat,
                        api_client=self._api_client,
                    )
                    synced += 1
                logger.info(f"✓ 堆场与校准同步完成，共 {synced} 个堆场，备份已更新")
                return

            logger.warning("⚠ API 堆场数据为空，沿用本地备份")
            backup = load_backup(self._yard_backup_path)
            if backup.get("yards"):
                self._yard_backup = backup
                self._yard_manager.reload_yards(backup["yards"])
                self._yard_origin_lat, spacing = build_indices(backup)
                self._calibration_ctx.update_bay_spacing(spacing)
                self._apply_calibration_snapshots(backup)
        except Exception as e:
            logger.exception(f"✗ 堆场同步异常: {e}")

    # ------------------------------------------------------------------
    # 辅助功能
    # ------------------------------------------------------------------
    def _on_mqtt_connected(self) -> None:
        """MQTT 连接成功或重连后上报设备上线。"""
        if not self._device_mgr.is_initialized():
            logger.warning("⚠ 设备未初始化，发送 discovery 上线广播...")
            self._broadcast_discovery()
        else:
            self._publish_online_status()

    def _broadcast_discovery(self) -> None:
        """发送设备发现广播（未初始化设备）。"""
        device_info = self._device_mgr.get_device_info()
        device_info["timestamp"] = time.time()
        device_info["sn"] = device_info.get("mac", "")
        logger.info(f"→ 发送设备发现广播: temp_id={device_info.get('temp_id')}")
        self._mqtt_client.publish_discovery(device_info)

    def _publish_online_status(self) -> None:
        """正式设备上线：rtg/{vehicle_id}/status，后端转发给前端 WebSocket。"""
        if not self._mqtt_client.is_connected():
            logger.debug("MQTT 未连接，跳过上线状态上报")
            return
        uptime = round(time.time() - self._boot_time, 1)
        payload = {
            "event": "online",
            "vehicle_id": self._vehicle_id,
            "timestamp": time.time(),
            "uptime": uptime,
            "mac": self._device_mgr.get_mac_address(),
        }
        logger.info(f"→ 上报设备上线: vehicle_id={self._vehicle_id}, uptime={uptime}s")
        self._mqtt_client.publish_status(payload)

    def _sync_config_from_cloud(self) -> None:
        """从云端同步车辆静态参数（启动时调用）。"""
        logger.info("→ 正在从云端同步车辆配置...")
        try:
            config = self._api_client.get_vehicle_config(self._vehicle_id)
            if config:
                self._apply_vehicle_config(config)
                logger.info("✓ 云端车辆配置已应用到解算器并写入本地备份")
            else:
                logger.warning("⚠ 云端配置获取失败，使用本地 vehicle_params.yaml")
        except Exception as e:
            logger.exception(f"✗ 配置同步异常: {e}")

def main():
    """主函数入口。"""
    system = RTGSystem()
    system.start()
    system.run_forever()


if __name__ == "__main__":
    main()

