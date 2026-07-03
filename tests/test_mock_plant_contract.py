import numpy as np
import pytest

from flow_control.mock_plant import MockPlant, MockPlantConfig
from flow_control.run_mock_demo import _mock_actuation_schedule_rows, _mock_timeseries_rows
from flow_control.schedule_generator import ActuationConfig
from starccm_control.control_spec import GLOBAL_OUTPUT_COLUMNS, JET_COLUMNS, LOAD_COLUMNS


def test_mock_plant_is_fixed_24_input_6_output_contract():
    config = MockPlantConfig()
    plant = MockPlant(config).reset(seed=123)

    assert config.n_inputs == 24
    assert config.n_outputs == 6
    assert plant.B.shape[1] == 24
    assert plant.C.shape[0] == 6

    output = plant.step(np.zeros(24))
    assert output.shape == (6,)

    with pytest.raises(ValueError, match="shape"):
        plant.step(np.zeros(23))

    with pytest.raises(ValueError, match="24 inputs"):
        MockPlantConfig(n_inputs=23)

    with pytest.raises(ValueError, match="6 outputs"):
        MockPlantConfig(n_outputs=5)


def test_mock_demo_maps_six_outputs_to_real_cfd_timeseries_columns():
    actuation = ActuationConfig(
        n_jets=24,
        n_active_per_window=3,
        n_excitation_windows=1,
        n_reference_windows=0,
        command_amplitude=1.0,
        window_duration=1.0,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=1,
        output_dir="runs/test_mock_contract",
    )
    inputs = np.zeros((24, 1), dtype=float)
    inputs[[0, 5, 23], 0] = 1.0
    outputs = np.asarray([[1.0], [1.2], [2.0], [2.2], [3.0], [3.2]], dtype=float)

    rows = _mock_timeseries_rows(
        actuation,
        inputs,
        outputs,
        stability={"stable": True},
    )

    row = rows[0]
    assert list(JET_COLUMNS) == [column for column in JET_COLUMNS if column in row]
    assert list(LOAD_COLUMNS) == [column for column in LOAD_COLUMNS if column in row]
    assert list(GLOBAL_OUTPUT_COLUMNS) == [column for column in GLOBAL_OUTPUT_COLUMNS if column in row]
    assert [row[column] for column in LOAD_COLUMNS] == [1.0, 1.2, 2.0, 2.2, 3.0, 3.2]
    assert row["Fz_Total"] == pytest.approx(12.6)
    assert row["Drag_Total"] == pytest.approx(float(np.sqrt(np.mean(outputs[:, 0] ** 2))))
    assert row["Pitch_Moment"] == pytest.approx(4.0)
    assert row["Roll_Moment"] == pytest.approx(0.6)
    assert row["Jet_Reaction_Z"] == pytest.approx(3.0)
    assert row["solver_status"] == "success"


def test_mock_demo_rounds_physical_time_window_bounds():
    actuation = ActuationConfig(
        n_jets=24,
        n_active_per_window=3,
        n_excitation_windows=7,
        n_reference_windows=0,
        command_amplitude=1.0,
        window_duration=0.1,
        max_consecutive_on=2,
        equal_activation_count=True,
        random_seed=1,
        output_dir="runs/test_mock_contract",
    )
    inputs = np.zeros((24, 7), dtype=float)
    outputs = np.zeros((6, 7), dtype=float)

    schedule_rows = _mock_actuation_schedule_rows(actuation, inputs)
    timeseries_rows = _mock_timeseries_rows(
        actuation,
        inputs,
        outputs,
        stability={"stable": True},
    )

    assert schedule_rows[3]["physical_time"] == 0.3
    assert schedule_rows[3]["t_start"] == 0.3
    assert schedule_rows[3]["t_end"] == 0.4
    assert schedule_rows[6]["physical_time"] == 0.6
    assert schedule_rows[6]["t_start"] == 0.6
    assert schedule_rows[6]["t_end"] == 0.7
    assert timeseries_rows[3]["physical_time"] == 0.3
    assert timeseries_rows[6]["physical_time"] == 0.6
