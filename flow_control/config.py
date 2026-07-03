"""Shared configuration loading for flow-control workflows."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_SYSTEM_CONFIG_PATH = Path("configs/system.yaml")
SYSTEM_CONFIG_ENV = "FLOW_CONTROL_SYSTEM_CONFIG"


def read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_system_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the shared system config, returning an empty mapping when absent."""

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
    """Merge shared system defaults with a task-specific config.

    Values from the task-specific config take precedence over the shared system
    config. Nested dictionaries are merged recursively.
    """

    system_config = load_system_config(system_config_path)
    local_config = read_yaml(path)
    return merge_config(system_config, local_config)


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(merged.get(key), dict)
        ):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged
