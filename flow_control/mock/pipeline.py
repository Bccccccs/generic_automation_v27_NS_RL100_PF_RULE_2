"""Workflow glue from actuation pattern generation to the mock plant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..generator import generate_from_yaml
from .mock_plant import write_mock_dynamic_case


def run_actuation_to_mock(
    *,
    actuation_config_path: str | Path,
    mock_config_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Generate ``<output_dir>/input`` and run the mock into ``<output_dir>``."""

    actuation_config = generate_from_yaml(
        actuation_config_path,
        output_dir=Path(output_dir),
    )
    schedule_path = actuation_config.output_dir / "actuation_schedule.csv"
    return write_mock_dynamic_case(
        schedule_path=schedule_path,
        config_path=mock_config_path,
        output_dir=output_dir,
    )
