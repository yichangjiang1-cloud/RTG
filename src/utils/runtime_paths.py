"""中控运行路径。

开发机不设环境变量，仍读项目里的 config/。
车上由 systemd 设置（见 deploy/rtg-controller.service）：

  RTG_CONFIG_DIR=/home/guide/CLionProjects/GNSS/config   只放 system.yaml，升级不覆盖
  RTG_STATE_DIR=/home/guide/CLionProjects/GNSS/state     程序写的 vehicle_params.yaml、yard_config.yaml
"""

from __future__ import annotations

import os
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("RTG_CONFIG_DIR", "config"))


def state_dir() -> Path:
    return Path(os.environ.get("RTG_STATE_DIR", "config"))


def system_yaml_path() -> Path:
    return config_dir() / "system.yaml"


def vehicle_params_path() -> Path:
    return state_dir() / "vehicle_params.yaml"


def yard_config_path() -> Path:
    return state_dir() / "yard_config.yaml"
