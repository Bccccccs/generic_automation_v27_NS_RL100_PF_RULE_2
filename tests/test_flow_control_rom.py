import csv

from flow_control.rom import (
    MASSFLOW_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    ARXModel,
    train_arx_rom_from_dataset,
    train_arx_rom_from_case,
    validate_arx_rom,
)
from flow_control.rom.generate_arx_dataset import generate_arx_sparse24_dataset


def test_train_arx_rom_from_case_writes_outputs(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    out_dir = tmp_path / "rom"

    _write_minimal_case(case_dir, row_count=24)

    result = train_arx_rom_from_case(
        case_dir=case_dir,
        out_dir=out_dir,
        train_fraction=0.70,
        input_lags=2,
        output_lags=2,
        ridge_alpha=1.0,
    )

    assert result.train_rows > result.validation_rows
    assert result.model_path.exists()
    assert result.metrics_path.exists()
    assert result.prediction_csv_path.exists()
    assert result.prediction_plot_path.exists()
    assert result.error_plot_path.exists()
    assert result.rmse_plot_path.exists()
    assert set(result.metrics) == set(ROM_OUTPUT_COLUMNS)


def test_legacy_arx_import_points_to_flow_control_rom():
    from models import ARXModel as LegacyModel

    assert LegacyModel is ARXModel


def test_generate_arx_sparse24_dataset_runs_schedule_and_mock(tmp_path):
    out_dir = tmp_path / "arx_dataset"

    records = generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=out_dir,
        count=2,
        start_seed=101,
    )

    assert [record["schedule_seed"] for record in records] == [101, 102]
    assert [record["mock_seed"] for record in records] == [101, 102]
    assert (out_dir / "index.csv").exists()
    assert (out_dir / "index.json").exists()
    for record in records:
        case_dir = out_dir / record["case_id"]
        assert (case_dir / "actuation_input" / "actuation_schedule.csv").exists()
        assert (case_dir / "actuation_schedule.csv").exists()
        assert (case_dir / "timeseries.csv").exists()
        assert (case_dir / "quality_report.json").exists()


def test_generate_arx_sparse24_dataset_defaults_to_one_seed_per_case(tmp_path):
    records = generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=tmp_path / "arx_dataset",
        count=2,
        start_seed=301,
    )

    assert [(record["schedule_seed"], record["mock_seed"]) for record in records] == [
        (301, 301),
        (302, 302),
    ]


def test_generate_arx_sparse24_dataset_reads_seed_from_system_config(tmp_path):
    system_config = tmp_path / "system.yaml"
    system_config.write_text(
        "system:\n"
        "  random_seed: 901\n",
        encoding="utf-8",
    )

    records = generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        system_config_path=system_config,
        output_dir=tmp_path / "arx_dataset",
        count=2,
    )

    assert [(record["global_seed"], record["schedule_seed"], record["mock_seed"]) for record in records] == [
        (901, 901, 901),
        (902, 902, 902),
    ]
    mock_config = (tmp_path / "arx_dataset" / "sparse24_seed_901" / "mock_config_used.yaml").read_text(
        encoding="utf-8"
    )
    assert "system:\n  random_seed: 901" in mock_config
    assert "mock_dynamic24x6:\n  random_seed:" not in mock_config


def test_train_dataset_and_validate_existing_model_are_separate(tmp_path):
    dataset_dir = tmp_path / "arx_dataset"
    generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=dataset_dir,
        count=2,
        start_seed=401,
    )

    train_result = train_arx_rom_from_dataset(
        dataset_dir=dataset_dir,
        out_dir=tmp_path / "train",
        input_lags=2,
        output_lags=2,
    )
    validate_result = validate_arx_rom(
        model_path=train_result.model_path,
        dataset_dir=dataset_dir,
        out_dir=tmp_path / "validate",
        case_start=1,
        case_count=1,
    )

    assert train_result.train_cases == 2
    assert train_result.validation_cases == 0
    assert train_result.model_path.exists()
    assert validate_result.case_count == 1
    assert validate_result.metrics_path.exists()
    assert validate_result.prediction_csv_path.exists()


def _write_minimal_case(case_dir, row_count: int) -> None:
    timeseries_columns = ["window_id", "physical_time", *ROM_INPUT_COLUMNS[:24], *ROM_OUTPUT_COLUMNS]
    schedule_columns = ["window_id", "physical_time", *MASSFLOW_COLUMNS]

    with (case_dir / "timeseries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=timeseries_columns)
        writer.writeheader()
        for idx in range(row_count):
            jets = {column: 1.0 if (idx + col_idx) % 5 == 0 else 0.0 for col_idx, column in enumerate(ROM_INPUT_COLUMNS[:24])}
            load_base = 0.2 * idx + 0.05 * sum(jets.values())
            outputs = {column: load_base + out_idx * 0.1 for out_idx, column in enumerate(ROM_OUTPUT_COLUMNS)}
            writer.writerow(
                {
                    "window_id": idx,
                    "physical_time": idx * 0.1,
                    **jets,
                    **outputs,
                }
            )

    with (case_dir / "actuation_schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=schedule_columns)
        writer.writeheader()
        for idx in range(row_count):
            massflow = {
                column: 0.03 if (idx + col_idx) % 5 == 0 else 0.0
                for col_idx, column in enumerate(MASSFLOW_COLUMNS)
            }
            writer.writerow(
                {
                    "window_id": idx,
                    "physical_time": idx * 0.1,
                    **massflow,
                }
            )
