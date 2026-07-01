from __future__ import annotations

from typing import Any


def load_runtime_service_settings(rl_config: dict[str, Any]) -> dict[str, Any]:
    from runtime_value_utils import as_bool
    return {
        "_intervention_enabled": as_bool(rl_config.get("intervention_enabled", False)),
    }
