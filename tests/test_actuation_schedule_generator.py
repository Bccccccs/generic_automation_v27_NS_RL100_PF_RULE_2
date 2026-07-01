from pathlib import Path

from flow_control.schedule_generator import (
    ActuationConfig,
    generate_actuation_matrix,
    validate_actuation_matrix,
    write_actuation_outputs,
)


def _config(seed: int = 20260618) -> ActuationConfig:
    return ActuationConfig(
        n_jets=24,
        n_active_per_window=3,
        n_excitation_windows=72,
        n_reference_windows=8,
        command_amplitude=1.0,
        window_duration=1.0,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=seed,
        output_dir=Path("runs/test_sparse24"),
    )


def test_actuation_schedule_constraints_and_reproducibility():
    config = _config()
    matrix = generate_actuation_matrix(config)

    assert validate_actuation_matrix(config, matrix) == []
    assert matrix == generate_actuation_matrix(config)
    assert matrix != generate_actuation_matrix(_config(seed=config.random_seed + 1))


def test_actuation_outputs_are_written(tmp_path):
    config = ActuationConfig(
        n_jets=24,
        n_active_per_window=3,
        n_excitation_windows=72,
        n_reference_windows=8,
        command_amplitude=1.0,
        window_duration=1.0,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=20260618,
        output_dir=tmp_path,
    )
    matrix = generate_actuation_matrix(config)

    write_actuation_outputs(config, matrix)

    expected_files = {
        "actuation_schedule.csv",
        "actuation_heatmap.svg",
        "activation_counts.csv",
        "pairwise_cooccurrence.csv",
        "input_correlation_matrix.csv",
        "mass_flow.csv",
        "total_load_curve.csv",
        "total_load_curve.svg",
        "spatial_nonuniformity_curve.csv",
        "spatial_nonuniformity_curve.svg",
        "case_manifest.yaml",
        "timeseries.csv",
        "quality_report.json",
        "config_summary.yaml",
        "validation_report.json",
    }
    assert expected_files <= {path.name for path in tmp_path.iterdir()}
    assert (tmp_path / "logs" / "case_io.log").exists()
    assert {
        "actuation_heatmap.svg",
        "total_load_curve.svg",
        "spatial_nonuniformity_curve.svg",
    } <= {path.name for path in (tmp_path / "figures").iterdir()}
