#!/usr/bin/env python3
"""从后端 API 拉取堆场全量数据并写入 config/yard_config.yaml。"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.config_loader import init_config, get_config
from src.bridge.api_client import ApiClient
from src.utils.yard_backup import fetch_from_api, save_backup, DEFAULT_BACKUP_PATH


def main():
    init_config("config/system.yaml")
    cfg = get_config()
    base = cfg.get("backend.api_base_url", "http://localhost:8000")
    path = cfg.get("yard_backup.path", DEFAULT_BACKUP_PATH)
    vehicle_id = sys.argv[1] if len(sys.argv) > 1 else "RTG-001"

    api = ApiClient(base)
    if not api.health_check():
        print("后端不可达，请先启动 server", file=sys.stderr)
        sys.exit(1)

    backup = fetch_from_api(api, vehicle_id)
    if not backup or not backup.get("yards"):
        print("未获取到堆场数据", file=sys.stderr)
        sys.exit(1)

    save_backup(backup, path)
    print(f"已同步 {len(backup['yards'])} 个堆场 -> {path}")
    for yid, block in (backup.get("yard_data") or {}).items():
        print(
            f"  {yid}: RTK={block.get('rtk_point_count', 0)} "
            f"transforms={len(block.get('transform_list') or [])} "
            f"mode={block.get('calibration_mode')}"
        )


if __name__ == "__main__":
    main()
