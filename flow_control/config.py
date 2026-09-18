"""共享配置加载模块，为 flow-control 工作流提供统一配置管理。

配置层级（优先级从低到高）：
1. 系统级 YAML（configs/system.yaml），由 FLOW_CONTROL_SYSTEM_CONFIG 环境变量覆盖路径
2. 任务级 YAML（具体任务的配置文件）
两者合并时，任务级配置会覆盖系统级配置中的同名字段（深度递归合并）。
"""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

# 默认系统配置文件路径，可通过环境变量 FLOW_CONTROL_SYSTEM_CONFIG 覆盖
DEFAULT_SYSTEM_CONFIG_PATH = Path("configs/system.yaml")
SYSTEM_CONFIG_ENV = "FLOW_CONTROL_SYSTEM_CONFIG"


def read_yaml(path: str | Path) -> dict[str, Any]:
    """读取 YAML 文件并返回字典，文件不存在或内容为空时返回空字典。"""
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_system_config(path: str | Path | None = None) -> dict[str, Any]:
    """加载共享系统配置，配置文件不存在时返回空字典而不报错。

    Args:
        path: 显式指定路径；为 None 时依次尝试环境变量和默认路径。

    Returns:
        解析后的配置字典，或空字典 {}。
    """
    raw_path = path or os.environ.get(SYSTEM_CONFIG_ENV) or DEFAULT_SYSTEM_CONFIG_PATH
    config_path = Path(raw_path)
    if not config_path.exists():
        return {}
    return read_yaml(config_path)


def load_config_with_system_defaults(
    path: str | Path,
    *,
    system_config_path: str | Path | None = None,
) -> dict[str, Any]:
    """加载任务级配置并与系统级默认配置合并。

    任务级配置的值会覆盖系统级同名字段。
    嵌套字典采用递归合并，而非简单替换。

    Args:
        path: 任务级 YAML 配置路径。
        system_config_path: 系统配置路径，为 None 时使用默认路径。

    Returns:
        合并后的完整配置字典。
    """
    system_config = load_system_config(system_config_path)
    local_config = read_yaml(path)
    return merge_config(system_config, local_config)


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度递归合并两个配置字典。

    对嵌套字典采用递归合并（不替换内部字段），
    对非字典值直接替换。

    Args:
        base: 基础配置（系统级默认值）。
        override: 覆盖配置（任务级值，优先级更高）。

    Returns:
        合并后的配置字典。
    """
    merged = deepcopy(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            # 如果两边都是字典，递归合并
            merged[key] = merge_config(merged[key], value)
        else:
            # 非字典值直接覆盖
            merged[key] = deepcopy(value)
    return merged
