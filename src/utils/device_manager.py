"""
工具模块：设备身份管理
负责：
  1. 生成/读取设备 ID（vehicle_id）
  2. 设备初始化后的配置持久化
  3. 启动时的配置对齐（云端配置优先）
"""

import csv
import io
import os
import platform
import re
import subprocess
import uuid
import yaml
from typing import List, Optional, Dict, Any, Tuple

from src.utils.enhanced_logger import EnhancedLogger

logger = EnhancedLogger.get_logger(__name__)

# uuid.getnode() 在装了 VMware / Hyper-V 的 Windows 上经常拿到虚拟网卡，
# 会把本机错认成另一台已绑定的车。优先挑物理有线网卡。
_VIRTUAL_HINTS = (
    "vmware", "virtual", "hyper-v", "vethernet", "wsl", "tap-", "tunnel",
    "anyconnect", "meta ", "bluetooth", "loopback", "pseudo", "npcap",
)
_WIRED_HINTS = (
    "asix", "realtek", "intel(r) ethernet", "ethernet", "gigabit",
    "usb to", "lan", "有线", "以太网",
)


class DeviceManager:
    """
    设备管理器：处理设备身份初始化、配置同步等。
    
    工作流程：
      1. 读取本地 system.yaml 中的 vehicle_id
      2. 若为 null / RTG-NEW-*，生成临时 ID（RTG-NEW-xxxx）
      3. 若本地为正式 ID，启动时向后端对账（须存在、initialized，且 temp_id/sn 与本机 MAC 一致）
      4. 等待后端下发正式 ID 后，保存到本地并由主程序热更新（无需重启）
    """

    def __init__(self, config_path: str = "config/system.yaml"):
        """
        :param config_path: system.yaml 配置文件路径
        """
        self._config_path = config_path
        self._vehicle_id: Optional[str] = None
        self._is_initialized: bool = False
        self._mac_address: str = self._get_mac_address()

        logger.info(f"DeviceManager 初始化，config_path={config_path}, MAC={self._mac_address}")

    def load_vehicle_id(self) -> str:
        """
        从配置文件加载 vehicle_id。
        若未初始化，返回临时 ID（RTG-NEW-xxxx）。
        
        :return: vehicle_id（正式或临时）
        """
        logger.info(f"→ 从配置文件加载 vehicle_id: {self._config_path}")
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            
            vid = data.get("vehicle_id")
            if vid and isinstance(vid, str) and not vid.startswith("RTG-NEW"):
                # 已初始化：使用正式 ID
                self._vehicle_id = vid
                self._is_initialized = True
                logger.info(f"✓ 设备已初始化，Vehicle ID: {vid}")
            else:
                # 未初始化：生成临时 ID
                self._vehicle_id = self._generate_temp_id()
                self._is_initialized = False
                logger.warning(f"⚠ 设备未初始化，使用临时 ID: {self._vehicle_id}")

            return self._vehicle_id

        except FileNotFoundError:
            logger.error(f"✗ 配置文件不存在: {self._config_path}")
            self._vehicle_id = self._generate_temp_id()
            self._is_initialized = False
            return self._vehicle_id
        except Exception as e:
            logger.exception(f"✗ 加载配置失败: {e}")
            self._vehicle_id = self._generate_temp_id()
            self._is_initialized = False
            return self._vehicle_id

    def save_vehicle_id(self, new_vehicle_id: str) -> bool:
        """
        将正式 vehicle_id 写入配置文件（设备初始化后调用）。
        
        :param new_vehicle_id: 后端下发的正式 ID
        :return: 成功返回 True
        """
        logger.info(f"→ 保存正式 Vehicle ID: {new_vehicle_id} 到 {self._config_path}")
        try:
            # 读取现有配置
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                data = {}
                logger.warning(f"⚠ 配置文件不存在，将创建新文件")

            # 更新 vehicle_id
            data["vehicle_id"] = new_vehicle_id

            # 写回文件
            os.makedirs(os.path.dirname(self._config_path) or ".", exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

            self._vehicle_id = new_vehicle_id
            self._is_initialized = True

            logger.info(f"✓ Vehicle ID 已保存到本地: {new_vehicle_id}")
            return True

        except Exception as e:
            logger.exception(f"✗ 保存 Vehicle ID 失败: {e}")
            return False

    def save_gnss_endpoints(self, endpoints: Dict[str, Dict[str, Any]]) -> bool:
        """把热更新后的接收机 host/port/transport 写回 system.yaml 的 gnss 段。"""
        if not endpoints:
            return False
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                data = {}
            gnss = data.get("gnss") if isinstance(data.get("gnss"), dict) else {}
            changed = False
            for key, fields in endpoints.items():
                if not isinstance(fields, dict):
                    continue
                block = gnss.get(key) if isinstance(gnss.get(key), dict) else {}
                for name in ("host", "port", "transport"):
                    if name not in fields or fields[name] is None:
                        continue
                    value = int(fields[name]) if name == "port" else str(fields[name])
                    if block.get(name) != value:
                        block[name] = value
                        changed = True
                gnss[key] = block
            if not changed:
                return False
            data["gnss"] = gnss
            os.makedirs(os.path.dirname(self._config_path) or ".", exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.info("✓ GNSS 端口已写回 %s: %s", self._config_path, list(endpoints))
            return True
        except Exception as e:
            logger.exception(f"✗ 写回 GNSS 端口失败: {e}")
            return False

    def reconcile_with_backend(self, api_client) -> str:
        """
        启动时与后端对账本地身份。
        本地为正式 ID 时，要求后端存在、initialized=true，且绑定本机硬件（temp_id 或 sn/MAC）。
        否则清除本地 ID 并退回临时 ID。后端不可达时不修改本地状态（离线容错）。
        """
        if not self._is_initialized or not self._vehicle_id:
            return self._vehicle_id

        formal_id = self._vehicle_id
        try:
            if not api_client.health_check():
                logger.warning("⚠ 后端不可达，跳过身份对账，沿用本地 Vehicle ID")
                return self._vehicle_id

            reg = api_client.get_vehicle_registration(formal_id)
            if reg is None:
                logger.warning(
                    f"⚠ 后端无车辆 {formal_id} 记录（可能库已重置），清除本地正式 ID"
                )
                return self._reset_to_temp_id()

            if not reg.get("initialized", False):
                logger.warning(
                    f"⚠ 后端车辆 {formal_id} 未完成初始化，清除本地正式 ID"
                )
                return self._reset_to_temp_id()

            if not self._owns_registration(reg):
                logger.warning(
                    f"⚠ 后端 {formal_id} 已绑定其他设备 "
                    f"(temp_id={reg.get('temp_id')}, sn={reg.get('sn')})，"
                    f"本机 temp_id={self._generate_temp_id()}, mac={self._mac_address}，清除本地正式 ID"
                )
                return self._reset_to_temp_id()

            logger.info(f"✓ 身份对账通过: {formal_id}（硬件绑定一致）")
            return self._vehicle_id
        except Exception as e:
            logger.warning(f"⚠ 身份对账异常，沿用本地 Vehicle ID: {e}")
            return self._vehicle_id

    def is_initialized(self) -> bool:
        """返回设备是否已初始化。"""
        return self._is_initialized

    def get_vehicle_id(self) -> Optional[str]:
        """返回当前 vehicle_id。"""
        return self._vehicle_id

    def get_mac_address(self) -> str:
        """返回 MAC 地址（用于唯一标识）。"""
        return self._mac_address

    def get_device_info(self) -> Dict[str, Any]:
        """
        返回设备信息字典（用于发现广播）。
        
        :return: {temp_id, mac, is_initialized, ...}
        """
        return {
            "temp_id": self._vehicle_id,
            "mac": self._mac_address,
            "is_initialized": self._is_initialized,
            "timestamp": None,  # 由调用方填充
        }

    @staticmethod
    def _format_mac(mac_hex: str) -> str:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", mac_hex)
        if len(cleaned) != 12:
            return ""
        return ":".join(cleaned[i:i + 2].lower() for i in range(0, 12, 2))

    @classmethod
    def _is_virtual_adapter(cls, name: str, description: str) -> bool:
        text = f"{name} {description}".lower()
        return any(hint in text for hint in _VIRTUAL_HINTS)

    @classmethod
    def _wired_score(cls, name: str, description: str) -> int:
        """分数越高越优先：物理有线 > 其它物理 > 不要虚拟。"""
        text = f"{name} {description}".lower()
        if cls._is_virtual_adapter(name, description):
            return -100
        score = 10
        if any(hint in text for hint in _WIRED_HINTS):
            score += 50
        if "wi-fi" in text or "wifi" in text or "wireless" in text or "wlan" in text:
            score -= 20
        return score

    @classmethod
    def _adapters_from_getmac(cls) -> List[Tuple[str, str, str]]:
        """Windows getmac：返回 (连接名, 适配器描述, mac)。"""
        if platform.system().lower() != "windows":
            return []
        try:
            raw = subprocess.check_output(
                ["getmac", "/v", "/fo", "csv"],
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            return []
        text = None
        for enc in ("utf-8", "gbk", "cp936"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = raw.decode("utf-8", errors="replace")
        rows: List[Tuple[str, str, str]] = []
        reader = csv.reader(io.StringIO(text))
        header = True
        for cols in reader:
            if header:
                header = False
                continue
            if len(cols) < 3:
                continue
            name, description, address = cols[0].strip(), cols[1].strip(), cols[2].strip()
            if not address or address.upper() in ("N/A", "NA"):
                continue
            mac = cls._format_mac(address)
            if mac:
                rows.append((name, description, mac))
        return rows

    @classmethod
    def _get_mac_address(cls) -> str:
        """
        获取本机用于车辆绑定的 MAC。
        Windows 优先选物理有线网卡（如港迪 ASIX USB），避开 VMware / Hyper-V。
        其它系统或枚举失败时回退 uuid.getnode()。
        """
        adapters = cls._adapters_from_getmac()
        if adapters:
            ranked = sorted(
                adapters,
                key=lambda item: cls._wired_score(item[0], item[1]),
                reverse=True,
            )
            best_name, best_desc, best_mac = ranked[0]
            score = cls._wired_score(best_name, best_desc)
            if score >= 0:
                logger.info(
                    "选用网卡 MAC=%s（%s / %s，score=%d）",
                    best_mac, best_name, best_desc, score,
                )
                return best_mac
            logger.warning(
                "仅找到疑似虚拟网卡，暂用 %s（%s）", best_mac, best_desc,
            )
            return best_mac

        mac = uuid.getnode()
        mac_hex = f"{mac:012x}"
        formatted = ":".join(mac_hex[i:i + 2] for i in range(0, 12, 2))
        logger.warning("网卡枚举失败，回退 uuid.getnode()=%s", formatted)
        return formatted

    def _generate_temp_id(self) -> str:
        """生成临时 ID（基于 MAC 地址后4位）。"""
        mac_suffix = self._mac_address.replace(":", "")[-4:]
        return f"RTG-NEW-{mac_suffix}"

    @staticmethod
    def _normalize_mac(value: str) -> str:
        return value.replace(":", "").replace("-", "").lower().strip()

    def _owns_registration(self, reg: Dict[str, Any]) -> bool:
        """后端车辆记录是否绑定到本机（temp_id 或 sn/MAC 任一匹配即可）。"""
        expected_temp = self._generate_temp_id()
        backend_temp = (reg.get("temp_id") or "").strip()
        if backend_temp and backend_temp == expected_temp:
            return True

        local_mac = self._normalize_mac(self._mac_address)
        backend_sn = self._normalize_mac(reg.get("sn") or "")
        if backend_sn and backend_sn == local_mac:
            return True

        if not backend_temp and not backend_sn:
            logger.warning("⚠ 后端记录缺少 temp_id/sn，无法校验设备归属")
            return False

        return False

    def _reset_to_temp_id(self) -> str:
        """清除本地正式 vehicle_id，退回临时 ID 并写回 system.yaml。"""
        temp_id = self._generate_temp_id()
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                data = {}
            data["vehicle_id"] = None
            os.makedirs(os.path.dirname(self._config_path) or ".", exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)
            self._vehicle_id = temp_id
            self._is_initialized = False
            logger.info(f"✓ 已退回临时 ID: {temp_id}，等待 discovery + 前端初始化")
            return temp_id
        except Exception as e:
            logger.exception(f"✗ 清除本地 Vehicle ID 失败: {e}")
            self._vehicle_id = temp_id
            self._is_initialized = False
            return temp_id


