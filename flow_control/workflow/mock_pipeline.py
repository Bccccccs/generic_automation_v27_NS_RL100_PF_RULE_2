"""Workflow glue from actuation pattern generation to the mock plant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..mock import write_mock_dynamic_case
from .schedule_generator import run_from_yaml


def run_actuation_to_mock(
    *,
    actuation_config_path: str | Path,
    mock_config_path: str | Path,
    schedule_output_dir: str | Path,
    mock_output_dir: str | Path,
) -> dict[str, Any]:
    """Generate actuation_schedule.csv from an actuation config, then run mock."""

    actuation_config = run_from_yaml(
        actuation_config_path,
        output_dir=Path(schedule_output_dir),
    )
    schedule_path = actuation_config.output_dir / "actuation_schedule.csv"
    return write_mock_dynamic_case(
        schedule_path=schedule_path,
        config_path=mock_config_path,
        output_dir=mock_output_dir,
    )
