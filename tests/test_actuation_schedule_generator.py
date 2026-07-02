from pathlib import Path
from itertools import groupby

from flow_control.schedule_generator import (
    ActuationConfig,
    activation_counts,
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
    excitation = matrix[: config.n_excitation_windows]
    references = matrix[config.n_excitation_windows :]
    combinations = [tuple(idx for idx, value in enumerate(row) if value) for row in excitation]
    max_consecutive_by_jet = [
        max(
            (len(list(group)) for value, group in groupby(row[jet_idx] for row in matrix) if value),
            default=0,
        )
        for jet_idx in range(config.n_jets)
    ]

    assert config.n_jets == 24
    assert len(matrix) == 80
    assert len(excitation) == 72
    assert len(references) == 8
    assert all(len(row) == 24 for row in matrix)
    assert {sum(row) for row in excitation} == {3}
    assert all(sum(row) == 0 for row in references)
    assert activation_counts(config, matrix) == [9] * 24
    assert len(set(combinations)) == len(combinations)
    assert max(max_consecutive_by_jet) <= config.max_consecutive_on
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
