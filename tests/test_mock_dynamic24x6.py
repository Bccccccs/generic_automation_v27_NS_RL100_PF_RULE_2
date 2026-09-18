import csv

from flow_control.data_schema import CaseSchema
from flow_control.mock import (
    MockDynamic24x6Config,
    MockDynamicPlant24x6,
    read_actuation_schedule,
    write_mock_dynamic_case,
)
from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS


def _write_schedule(path):
    columns = [
        "time",
        "window_id",
        "t_start",
        "t_end",
        *JET_COLUMNS,
        *(f"cmd_massflow_{idx:02d}" for idx in range(1, 25)),
    ]
    rows = []
    for window_id in range(5):
        row = {
            "time": window_id * 0.1,
            "window_id": window_id,
            "t_start": window_id * 0.1,
            "t_end": (window_id + 1) * 0.1,
        }
        for idx, column in enumerate(JET_COLUMNS, start=1):
            active = int((window_id == 1 and idx in {1, 2, 3, 4}) or (window_id == 3 and idx in {17, 18}))
            row[column] = active
            row[f"cmd_massflow_{idx:02d}"] = 0.25 if active else 0.0
        rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_mock_dynamic24x6_is_schedule_driven_and_schema_compatible(tmp_path):
    schedule_path = tmp_path / "actuation_schedule.csv"
    config_path = tmp_path / "mock_dynamic24x6.yaml"
    output_dir = tmp_path / "mock_dynamic24x6_demo"
    _write_schedule(schedule_path)
    config_path.write_text(
        "mock_dynamic24x6:\n"
        "  random_seed: 123\n"
        "  fz_noise_std: 0.0\n"
        "  drag_noise_std: 0.0\n"
        "  moment_noise_std: 0.0\n",
        encoding="utf-8",
    )

    result = write_mock_dynamic_case(
        schedule_path=schedule_path,
        config_path=config_path,
        output_dir=output_dir,
    )

    timeseries_path = result["files"]["timeseries"]
    with timeseries_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 5
    assert CaseSchema.validate_timeseries(rows) == []
    assert set(LOAD_COLUMNS).issubset(rows[0])
    assert rows[0]["solver_status"] == "success"
    assert (output_dir / "figures" / "input_heatmap.svg").exists()
    assert (output_dir / "figures" / "fz_regions.svg").exists()
    assert (output_dir / "figures" / "fz_total.svg").exists()
    assert (output_dir / "figures" / "spatial_nonuniformity.svg").exists()
    assert (output_dir / "figures" / "total_massflow.svg").exists()


def test_mock_dynamic24x6_fixed_seed_is_reproducible(tmp_path):
    schedule_path = tmp_path / "actuation_schedule.csv"
    _write_schedule(schedule_path)
    rows = read_actuation_schedule(schedule_path)
    config = MockDynamic24x6Config(random_seed=321)

    left = MockDynamicPlant24x6(config).simulate(rows)["timeseries"]
    right = MockDynamicPlant24x6(config).simulate(rows)["timeseries"]

    assert left == right


def test_mock_dynamic24x6_can_iterate_within_control_windows(tmp_path):
    schedule_path = tmp_path / "actuation_schedule.csv"
    _write_schedule(schedule_path)
    rows = read_actuation_schedule(schedule_path)
    config = MockDynamic24x6Config(
        random_seed=321,
        fz_noise_std=0.0,
        time_step=0.02,
    )

    result = MockDynamicPlant24x6(config).simulate(rows)
    timeseries = result["timeseries"]

    assert len(timeseries) == 25
    assert [int(float(row["window_id"])) for row in timeseries[:5]] == [0, 0, 0, 0, 0]
    assert [float(row["physical_time"]) for row in timeseries[:6]] == [0.0, 0.02, 0.04, 0.06, 0.08, 0.1]
    assert CaseSchema.validate_timeseries(timeseries) == []


def test_mock_dynamic24x6_reads_time_step_from_schedule_config(tmp_path):
    schedule_path = tmp_path / "input" / "actuation_schedule.csv"
    config_path = tmp_path / "mock_dynamic24x6.yaml"
    output_dir = tmp_path / "mock_from_existing_schedule"
    schedule_path.parent.mkdir()
    _write_schedule(schedule_path)
    (schedule_path.parent / "config_summary.yaml").write_text(
        "time_step_seconds: 0.02\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "mock_dynamic24x6:\n"
        "  random_seed: 123\n"
        "  fz_noise_std: 0.0\n"
        "  drag_noise_std: 0.0\n"
        "  moment_noise_std: 0.0\n",
        encoding="utf-8",
    )

    result = write_mock_dynamic_case(
        schedule_path=schedule_path,
        config_path=config_path,
        output_dir=output_dir,
    )

    with result["files"]["timeseries"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest = (output_dir / "case_manifest.yaml").read_text(encoding="utf-8")

    assert len(rows) == 25
    assert float(rows[1]["physical_time"]) == 0.02
    assert "time_step: 0.02" in manifest
    assert "time_step_source: config_summary" in manifest


def test_system_random_seed_drives_mock_config():
    base = MockDynamic24x6Config.from_mapping(
        {"system": {"random_seed": 4321}, "mock_dynamic24x6": {}}
    )
    override = MockDynamic24x6Config.from_mapping(
        {
            "system": {"random_seed": 4321},
            "mock_dynamic24x6": {"random_seed": 8765},
        }
    )

    assert base.random_seed == 4321
    assert override.random_seed == 8765


def test_mock_config_loads_shared_system_seed(tmp_path, monkeypatch):
    from flow_control.mock.mock_plant import load_config

    system_config = tmp_path / "system.yaml"
    mock_config = tmp_path / "mock.yaml"
    system_config.write_text(
        "system:\n"
        "  random_seed: 24680\n",
        encoding="utf-8",
    )
    mock_config.write_text(
        "mock_dynamic24x6:\n"
        "  fz_noise_std: 0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOW_CONTROL_SYSTEM_CONFIG", str(system_config))

    config = MockDynamic24x6Config.from_mapping(load_config(mock_config))

    assert config.random_seed == 24680
