"""

通信模块：后端 HTTP API 客户端

封装与后端服务的 REST API 交互

"""



import requests

from typing import Optional, Dict, Any, List



from src.utils.enhanced_logger import EnhancedLogger



logger = EnhancedLogger.get_logger(__name__)





def _unwrap_api_payload(resp_json: Any) -> Any:

    """解析 FastAPI ApiResponse 包装：{code, message, data}。"""

    if isinstance(resp_json, dict) and "data" in resp_json:

        return resp_json.get("data")

    return resp_json





class ApiClient:

    """后端 API 客户端"""



    def __init__(self, base_url: str, timeout: float = 10.0):

        self._base_url = base_url.rstrip("/")

        self._timeout = timeout

        self._session = requests.Session()

        logger.info(f"API Client 初始化完成: base_url={self._base_url}, timeout={timeout}s")



    def get_vehicle_registration(self, vehicle_id: str) -> Optional[Dict[str, Any]]:
        """查询车辆在后端的注册状态；404 表示未注册。"""
        url = f"{self._base_url}/api/vehicle/{vehicle_id}"
        logger.info(f"→ 查询车辆注册: vehicle_id={vehicle_id}")
        try:
            resp = self._session.get(url, timeout=self._timeout)
            if resp.status_code == 404:
                logger.warning(f"⚠ 后端未注册车辆: vehicle_id={vehicle_id}")
                return None
            resp.raise_for_status()
            data = _unwrap_api_payload(resp.json())
            logger.info(
                f"✓ 车辆注册查询成功: vehicle_id={vehicle_id}, "
                f"initialized={data.get('initialized') if isinstance(data, dict) else '?'}"
            )
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"✗ 查询车辆注册失败: vehicle_id={vehicle_id}, error={e}")
            raise

    def get_vehicle_config(self, vehicle_id: str) -> Optional[Dict[str, Any]]:

        """获取车辆配置"""

        url = f"{self._base_url}/api/vehicle/config/{vehicle_id}"

        logger.info(f"→ 请求车辆配置: vehicle_id={vehicle_id}")

        try:

            resp = self._session.get(url, timeout=self._timeout)

            resp.raise_for_status()

            config = _unwrap_api_payload(resp.json())

            logger.info(f"✓ 车辆配置获取成功: vehicle_id={vehicle_id}, 配置项数={len(config) if config else 0}")

            return config

        except Exception as e:

            logger.error(f"✗ 获取车辆配置失败: vehicle_id={vehicle_id}, error={e}")

            return None



    def update_vehicle_config(self, vehicle_id: str, config: Dict[str, Any]) -> bool:

        """更新车辆配置"""

        url = f"{self._base_url}/api/vehicle/config"

        logger.info(f"→ 更新车辆配置: vehicle_id={vehicle_id}, 配置项数={len(config)}")

        try:

            resp = self._session.post(url, json={"vehicle_id": vehicle_id, **config}, timeout=self._timeout)

            resp.raise_for_status()

            logger.info(f"✓ 车辆配置更新成功: vehicle_id={vehicle_id}")

            return True

        except Exception as e:

            logger.error(f"✗ 更新车辆配置失败: vehicle_id={vehicle_id}, error={e}")

            return False



    def get_all_yards(self) -> List[Dict[str, Any]]:

        """获取所有堆场"""

        url = f"{self._base_url}/api/yard/all_origins"

        logger.info("→ 请求所有堆场列表")

        try:

            resp = self._session.get(url, timeout=self._timeout)

            resp.raise_for_status()

            data = _unwrap_api_payload(resp.json())

            yards = data if isinstance(data, list) else []

            logger.info(f"✓ 堆场列表获取成功: 共 {len(yards)} 个堆场")

            return yards

        except Exception as e:

            logger.error(f"✗ 获取堆场列表失败: error={e}")

            return []



    def get_yard_map(self, yard_id: str, vehicle_id: str) -> Optional[Dict[str, Any]]:

        """获取堆场底图 / 校准上下文"""

        url = f"{self._base_url}/api/yard/map"

        logger.info(f"→ 请求堆场地图: yard_id={yard_id}, vehicle_id={vehicle_id}")

        try:

            resp = self._session.get(

                url,

                params={"yard_id": yard_id, "vehicle_id": vehicle_id},

                timeout=self._timeout,

            )

            resp.raise_for_status()

            map_data = _unwrap_api_payload(resp.json()) or {}

            has_rtk = bool(map_data.get("has_rtk_map"))

            mode = map_data.get("calibration_mode")

            logger.info(

                f"✓ 堆场地图获取成功: yard_id={yard_id}, RTK={has_rtk}, mode={mode}"

            )

            return map_data

        except Exception as e:

            logger.error(f"✗ 获取堆场地图失败: yard_id={yard_id}, error={e}")

            return None



    def get_transform_list(self, vehicle_id: str, yard_id: str) -> Optional[List[Dict[str, Any]]]:

        """获取逐贝转换列表"""

        url = f"{self._base_url}/api/calibration/transform_list"

        try:

            resp = self._session.get(

                url,

                params={"vehicle_id": vehicle_id, "yard_id": yard_id},

                timeout=self._timeout,

            )

            resp.raise_for_status()

            data = _unwrap_api_payload(resp.json()) or {}

            return data.get("transform_list") or []

        except Exception as e:

            logger.error(

                f"✗ 获取 transform_list 失败: vehicle={vehicle_id} yard={yard_id} error={e}"

            )

            return None



    def get_calibration_mode(self, vehicle_id: str, yard_id: str) -> Optional[Dict[str, Any]]:

        """查询校准模式"""

        url = f"{self._base_url}/api/calibration/mode"

        try:

            resp = self._session.get(

                url,

                params={"vehicle_id": vehicle_id, "yard_id": yard_id},

                timeout=self._timeout,

            )

            resp.raise_for_status()

            return _unwrap_api_payload(resp.json())

        except Exception as e:

            logger.error(f"✗ 获取校准模式失败: error={e}")

            return None



    def save_yard_map(self, yard_id: str, vehicle_id: str, m_matrix: List[List[float]], map_data: Optional[Dict] = None) -> bool:

        """保存堆场地图"""

        url = f"{self._base_url}/api/yard/save_map"

        payload = {"yard_id": yard_id, "vehicle_id": vehicle_id, "m_matrix": m_matrix, "map_data": map_data or {}}

        logger.info(f"→ 保存堆场地图: yard_id={yard_id}, vehicle_id={vehicle_id}")

        try:

            resp = self._session.post(url, json=payload, timeout=self._timeout)

            resp.raise_for_status()

            logger.info(f"✓ 堆场地图保存成功: yard_id={yard_id}")

            return True

        except Exception as e:

            logger.error(f"✗ 保存堆场地图失败: yard_id={yard_id}, error={e}")

            return False



    def confirm_survey_point(self, vehicle_id: str, yard_id: str, bay_id: str, mode: str) -> Optional[Dict[str, Any]]:

        """确认打点"""

        url = f"{self._base_url}/api/survey/confirm"

        payload = {"vehicle_id": vehicle_id, "yard_id": yard_id, "bay_id": bay_id, "mode": mode}

        logger.info(f"→ 确认打点: yard_id={yard_id}, bay_id={bay_id}, mode={mode}")

        try:

            resp = self._session.post(url, json=payload, timeout=self._timeout)

            resp.raise_for_status()

            result = resp.json()

            logger.info(f"✓ 打点确认成功: bay_id={bay_id}")

            return result

        except Exception as e:

            logger.error(f"✗ 打点确认失败: bay_id={bay_id}, error={e}")

            return None



    def health_check(self) -> bool:

        """健康检查"""

        logger.debug("→ 执行后端健康检查")

        try:

            resp = self._session.get(f"{self._base_url}/api/health", timeout=3.0)

            is_healthy = resp.status_code == 200

            if is_healthy:

                logger.debug("✓ 后端服务健康")

            else:

                logger.warning(f"⚠ 后端服务异常: status_code={resp.status_code}")

            return is_healthy

        except Exception as e:

            logger.error(f"✗ 健康检查失败: error={e}")

            return False



