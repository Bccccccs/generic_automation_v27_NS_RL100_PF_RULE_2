import csv
import hashlib
from itertools import groupby
from pathlib import Path

import pytest

from flow_control.excitation_patterns.common import (
    ActuationConfig,
    generate_pattern_table,
    rows_from_table,
    write_pattern_outputs,
)
from flow_control.excitation_patterns.sparse_groups import (
    activation_counts,
    generate_actuation_matrix,
    validate_sparse_matrix,
)
from flow_control.generator import generate_from_yaml
from flow_control.generator.schedule_validator import validate_actuation_schedule_csv


def _config(seed: int = 20260618) -> ActuationConfig:
    return ActuationConfig(
        n_jets=24,
        n_active_per_window=3,
        n_excitation_windows=72,
        n_reference_windows=8,
        mass_flow_rate=1.0,
        window_duration=1.0,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=seed,
        output_dir=Path("runs/test_sparse24"),
        mode="sparse_random_groups",
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
    assert validate_sparse_matrix(config, matrix) == []
    assert matrix == generate_actuation_matrix(config)
    assert matrix != generate_actuation_matrix(_config(seed=config.random_seed + 1))


def test_actuation_outputs_are_written(tmp_path):
    config = ActuationConfig(
        n_jets=24,
        mode="sparse_random_groups",
        n_active_per_window=3,
        n_excitation_windows=72,
        n_reference_windows=8,
        mass_flow_rate=0.02,
        window_duration=0.1,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=20260618,
        output_dir=tmp_path,
    )
    matrix = generate_actuation_matrix(config)

    table, _, errors = generate_pattern_table(config)
    assert errors == []
    write_pattern_outputs(config, table)

    expected_files = {
        "actuation_schedule.csv",
        "actuation_heatmap.svg",
        "total_mass_flow.csv",
        "total_mass_flow_curve.svg",
        "config_summary.yaml",
        "validation_report.json",
    }
    assert expected_files <= {path.name for path in tmp_path.iterdir()}

    with (tmp_path / "actuation_schedule.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert float(rows[0]["time"]) == 0.0
    assert list(rows[0]) == [
        "time",
        "window_id",
        "t_start",
        "t_end",
        *(f"JET_{idx:02d}" for idx in range(1, 25)),
        *(f"cmd_massflow_{idx:02d}" for idx in range(1, 25)),
    ]
    assert float(rows[1]["time"]) == 0.1
    assert float(rows[1]["t_start"]) == 0.1
    assert float(rows[1]["t_end"]) == 0.2
    for row in rows:
        jet_values = [int(float(row[f"JET_{idx:02d}"])) for idx in range(1, 25)]
        massflow_values = [float(row[f"cmd_massflow_{idx:02d}"]) for idx in range(1, 25)]
        active_values = [value for value in jet_values if value != 0]
        if int(row["window_id"]) < config.n_excitation_windows:
            assert len(active_values) == config.n_active_per_window
        else:
            assert active_values == []
        for jet_value, massflow_value in zip(jet_values, massflow_values):
            if jet_value == 0:
                assert massflow_value == 0.0
            else:
                assert massflow_value == config.mass_flow_rate
    assert validate_actuation_schedule_csv(tmp_path / "actuation_schedule.csv") == []


def test_pulse_and_step_patterns_use_physical_time_values():
    pulse = ActuationConfig(
        mode="pulse_singlejet",
        total_windows=4,
        window_duration=0.1,
        mass_flow_rate=0.02,
        jet_ids=(3,),
        pulse_windows=(1,),
    )
    pulse_table, _, errors = generate_pattern_table(pulse)
    assert errors == []
    assert [row[2] for row in pulse_table.switches] == [1, 0, 0, 0]
    assert [row[2] for row in pulse_table.massflows] == [0.02, 0.0, 0.0, 0.0]

    fifth_window_pulse = ActuationConfig(
        mode="pulse_singlejet",
        total_windows=10,
        window_duration=0.1,
        time_step=1.0e-4,
        mass_flow_rate=1.0,
        jet_ids=(2,),
        pulse_windows=(5,),
    )
    fifth_table, _, errors = generate_pattern_table(fifth_window_pulse)
    assert errors == []
    assert [idx for idx, row in enumerate(fifth_table.switches) if row[1]] == [4]


def test_explicit_time_and_actuation_window_config_fields_are_separate():
    config = ActuationConfig.from_mapping(
        {
            "actuation": {
                "mode": "pulse_singlejet",
                "total_actuation_windows": 10,
                "actuation_window_duration": 0.1,
                "solver_time_step": 1.0e-4,
                "jet_ids": [2],
                "pulse_window_numbers": [5],
            }
        }
    )

    assert config.total_actuation_windows == 10
    assert config.actuation_window_duration == 0.1
    assert config.solver_time_step == 1.0e-4
    assert config.pulse_window_numbers == (5,)
    table, _, errors = generate_pattern_table(config)
    assert errors == []
    assert [idx for idx, row in enumerate(table.switches) if row[1]] == [4]
    rows = rows_from_table(config, table)
    assert len(rows) == 10000
    assert rows[0]["time"] == 0.0
    assert rows[1]["time"] == 1.0e-4
    assert rows[-1]["t_end"] == 1.0
    pulse_rows = [row for row in rows if row["window_id"] == 4]
    assert len(pulse_rows) == 1000
    assert pulse_rows[0]["time"] == 0.4
    assert pulse_rows[-1]["t_start"] == 0.4999
    assert pulse_rows[-1]["t_end"] == 0.5
    assert all(row["JET_02"] == 1 for row in pulse_rows)


def test_conflicting_new_and_legacy_time_fields_are_rejected():
    with pytest.raises(ValueError, match="conflicting actuation fields"):
        ActuationConfig.from_mapping(
            {
                "actuation": {
                    "mode": "pulse_singlejet",
                    "actuation_window_duration": 0.1,
                    "window_duration": 1.0e-4,
                }
            }
        )

    step = ActuationConfig(
        mode="step_singlejet",
        total_windows=10,
        window_duration=0.1,
        mass_flow_rate=0.02,
        jet_ids=(3,),
        step_start_window=1,
        step_end_window=8,
    )
    step_table, _, errors = generate_pattern_table(step)
    assert errors == []
    assert [row[2] for row in step_table.switches] == [0, 1, 1, 1, 1, 1, 1, 1, 0, 0]


def test_prbs_reproducibility_and_seed_change():
    base = ActuationConfig(
        mode="prbs_demo",
        total_windows=40,
        window_duration=0.05,
        mass_flow_rate=0.02,
        random_seed=1234,
        max_active_jets=4,
        max_total_mass_flow=0.08,
    )
    same = ActuationConfig(
        mode="prbs_demo",
        total_windows=40,
        window_duration=0.05,
        mass_flow_rate=0.02,
        random_seed=1234,
        max_active_jets=4,
        max_total_mass_flow=0.08,
    )
    changed = ActuationConfig(
        mode="prbs_demo",
        total_windows=40,
        window_duration=0.05,
        mass_flow_rate=0.02,
        random_seed=1235,
        max_active_jets=4,
        max_total_mass_flow=0.08,
    )

    base_table, _, _ = generate_pattern_table(base)
    same_table, _, _ = generate_pattern_table(same)
    changed_table, _, _ = generate_pattern_table(changed)
    assert base_table.switches == same_table.switches
    assert base_table.switches != changed_table.switches
    assert all(sum(row) <= 4 for row in base_table.switches)
    assert all(sum(row) <= 0.08 + 1e-12 for row in base_table.massflows)


def test_config_driven_generation_writes_examples(tmp_path):
    config_path = tmp_path / "pulse.yaml"
    config_path.write_text(
        """
actuation:
  mode: pulse_singlejet
  n_jets: 24
  total_windows: 4
  window_duration: 0.1
  mass_flow_rate: 0.02
  jet_ids: [3]
  pulse_windows: [1]
  random_seed: 99
output:
  run_dir: ignored
""",
        encoding="utf-8",
    )
    generate_from_yaml(config_path, output_dir=tmp_path / "pulse_singlejet")
    schedule_path = tmp_path / "pulse_singlejet" / "input" / "actuation_schedule.csv"
    assert schedule_path.exists()
    assert validate_actuation_schedule_csv(schedule_path) == []


def test_system_random_seed_drives_actuation_config():
    base = ActuationConfig.from_mapping(
        {
            "system": {"random_seed": 1234},
            "actuation": {
                "mode": "prbs_demo",
                "total_windows": 12,
                "max_active_jets": 4,
            },
        }
    )
    override = ActuationConfig.from_mapping(
        {
            "system": {"random_seed": 1234},
            "actuation": {
                "mode": "prbs_demo",
                "total_windows": 12,
                "random_seed": 5678,
                "max_active_jets": 4,
            },
        }
    )

    assert base.random_seed == 1234
    assert override.random_seed == 5678


def test_generate_from_yaml_uses_shared_system_config(tmp_path, monkeypatch):
    system_config = tmp_path / "system.yaml"
    config_path = tmp_path / "prbs.yaml"
    output_dir = tmp_path / "prbs_out"
    system_config.write_text(
        "system:\n"
        "  random_seed: 777\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "actuation:\n"
        "  mode: prbs_demo\n"
        "  total_windows: 8\n"
        "  max_active_jets: 4\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOW_CONTROL_SYSTEM_CONFIG", str(system_config))

    config = generate_from_yaml(config_path, output_dir=output_dir)

    assert config.random_seed == 777
    assert (output_dir / "input" / "actuation_schedule.csv").exists()


@pytest.mark.parametrize(
    ("config_name", "expected_sha256"),
    [
        ("training", "55dfb6fe0fb36f2294e817621298cb4e9cb65cf0bfafb6d56514521c04974abb"),
        ("validation", "bd0122c74387dc01d562c978afedc81d1c55481cb9afc97ab1368f0ea0210cad"),
    ],
)
def test_b52_experiments_use_standard_actions_flow_without_changing_schedule(
    tmp_path, config_name, expected_sha256
):
    project_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / config_name

    generate_from_yaml(
        project_root / "configs" / "b52" / f"{config_name}.yaml",
        output_dir=output_dir,
    )

    schedule_path = output_dir / "input" / "actuation_schedule.csv"
    assert validate_actuation_schedule_csv(
        schedule_path,
        max_active_jets=1,
        max_total_mass_flow=2.86,
    ) == []
    assert hashlib.sha256(schedule_path.read_bytes()).hexdigest() == expected_sha256
