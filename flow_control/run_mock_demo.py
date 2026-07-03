"""Run the local actuation -> mock plant -> case bundle workflow."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from .actuation_workflow import load_actuation_run, read_yaml
from .excitation_patterns import ActuationConfig
from .mock_case_bundle import (
    append_time_ticks as _append_time_ticks,
    blend as _blend,
    copy_mock_figures_to_standard_dir as _copy_mock_figures_to_standard_dir,
    mock_actuation_schedule_rows as _mock_actuation_schedule_rows,
    mock_manifest as _mock_manifest,
    mock_timeseries_rows as _mock_timeseries_rows,
    svg_header as _svg_header,
    svg_text as _svg_text,
    window_time_bounds as _window_time_bounds,
    write_correlations_csv as _write_correlations_csv,
    write_input_heatmap_svg as _write_input_heatmap_svg,
    write_matrix_csv as _write_matrix_csv,
    write_mock_case_bundle,
    write_mock_schema_case as _write_mock_schema_case,
    write_output_timeseries_svg as _write_output_timeseries_svg,
    write_ranking_csv as _write_ranking_csv,
)
from .mock_plant import MockPlantConfig
from .mock_rollout import (
    impulse_response as _impulse_response,
    influence_ranking as _influence_ranking,
    input_output_correlations as _input_output_correlations,
    run_mock_rollout,
    run_plant as _run_plant,
    safe_corr as _safe_corr,
    stability_check as _stability_check,
)


DEFAULT_B04_OUTPUT_DIR = Path("runs/b04_mock_plant")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MockPlant CFD/RL validation demo.")
    parser.add_argument("--config", default="configs/pilot_sparse24.yaml")
    parser.add_argument("--output-dir", help="Override the B04 mock-plant output directory.")
    parser.add_argument("--seed", type=int, help="Override actuation random_seed for the plant.")
    argv = [arg.replace("–", "--", 1) if arg.startswith("–") else arg for arg in sys.argv[1:]]
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_B04_OUTPUT_DIR
    actuation_run = load_actuation_run(args.config, output_dir=output_dir)
    actuation = actuation_run.config

    plant_seed = int(args.seed if args.seed is not None else actuation.random_seed + 404)
    plant_config = _mock_config_from_mapping(actuation_run.raw_config)
    rollout = run_mock_rollout(
        actuation_run.inputs,
        plant_config=plant_config,
        plant_seed=plant_seed,
        window_duration=actuation.window_duration,
        command_amplitude=actuation.command_amplitude,
    )

    summary = write_mock_case_bundle(
        config_path=args.config,
        actuation=actuation,
        raw_config=actuation_run.raw_config,
        plant_config=plant_config,
        plant_seed=plant_seed,
        inputs=actuation_run.inputs,
        outputs=rollout.outputs,
        stability=rollout.stability,
        correlations=rollout.correlations,
        ranking=rollout.ranking,
    )

    print(f"MockPlant demo complete: {actuation.output_dir}")
    print(
        "Stable: "
        f"{summary['stability']['stable']} max_abs_y={summary['stability']['max_abs_y']:.4f}"
    )
    print("Top influence jets:", ", ".join(summary["hidden_jet_learning_check"]["top5_ranked_jets"]))


def _read_yaml(path: str | Path) -> dict[str, Any]:
    return read_yaml(path)


def _mock_config_from_mapping(data: dict[str, Any]) -> MockPlantConfig:
    values = data.get("mock_plant", {})
    allowed = {field.name for field in fields(MockPlantConfig)}
    kwargs = {key: value for key, value in values.items() if key in allowed}
    return MockPlantConfig(**kwargs)


def _replace_actuation_output_dir(config: ActuationConfig, output_dir: Path) -> ActuationConfig:
    return replace(config, output_dir=output_dir)


if __name__ == "__main__":
    main()
