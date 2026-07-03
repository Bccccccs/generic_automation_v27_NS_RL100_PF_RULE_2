"""Actuation schedule generation used by local flow-control workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .excitation_patterns import ActuationConfig, ScheduleTable, generate_pattern_table


@dataclass(frozen=True)
class ActuationRun:
    """Generated actuation commands in both table and matrix form."""

    raw_config: dict[str, Any]
    config: ActuationConfig
    table: ScheduleTable
    inputs: np.ndarray
    extra: dict[str, Any]


def load_actuation_run(config_path: str | Path, output_dir: str | Path | None = None) -> ActuationRun:
    raw_config = read_yaml(config_path)
    config = ActuationConfig.from_mapping(raw_config)
    if output_dir is not None:
        config = replace(config, output_dir=Path(output_dir))

    table, extra, errors = generate_pattern_table(config)
    if errors:
        raise RuntimeError(f"generated actuation failed validation: {errors}")
    inputs = np.asarray(table.massflows, dtype=float).T
    return ActuationRun(
        raw_config=raw_config,
        config=config,
        table=table,
        inputs=inputs,
        extra=extra,
    )


def read_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}

