import csv
import json

from flow_control.rom import (
    MASSFLOW_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    ARXModel,
    train_arx_rom_from_dataset,
    train_arx_rom_from_case,
    use_arx_rom_on_case,
    use_arx_rom_on_schedule,
    validate_arx_rom,
)
from flow_control.rom.generate_arx_dataset import generate_arx_sparse24_dataset


def test_train_arx_rom_from_case_uses_all_rows_and_writes_training_only_outputs(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    out_dir = tmp_path / "rom"

    _write_minimal_case(case_dir, row_count=24)

    result = train_arx_rom_from_case(
        case_dir=case_dir,
        out_dir=out_dir,
        input_lags=2,
        output_lags=2,
        ridge_alpha=1.0,
    )

    assert result.train_cases == 1
    assert result.source_rows == 24
    assert result.fit_rows == 22
    assert result.model_path.exists()
    assert result.training_summary_path.exists()
    assert not (out_dir / "metrics.json").exists()
    assert not (out_dir / "prediction_timeseries.csv").exists()
    assert not (out_dir / "prediction_6_load_cells.svg").exists()
    summary = json.loads(result.training_summary_path.read_text(encoding="utf-8"))
    assert summary["validation_performed"] is False
    assert summary["source_rows"] == 24
    assert summary["fit_rows"] == 22
    assert summary["fit_policy"].endswith("no internal split")


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
        assert (case_dir / "input" / "actuation_schedule.csv").exists()
        assert (case_dir / "actuation_schedule.csv").exists()
        assert (case_dir / "timeseries.csv").exists()
        assert (case_dir / "quality_report.json").exists()
        with (case_dir / "timeseries.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 160
        assert float(rows[1]["physical_time"]) == 0.05
        assert record["time_step"] == 0.05


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
    assert "time_step: 0.05" in mock_config
    assert "mock_dynamic24x6:\n  random_seed:" not in mock_config


def test_train_dataset_and_validate_existing_model_are_separate(tmp_path):
    train_dataset_dir = tmp_path / "arx_train_dataset"
    validation_dataset_dir = tmp_path / "arx_validation_dataset"
    generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=train_dataset_dir,
        count=2,
        start_seed=401,
    )
    generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=validation_dataset_dir,
        count=1,
        start_seed=501,
    )

    train_result = train_arx_rom_from_dataset(
        dataset_dir=train_dataset_dir,
        out_dir=tmp_path / "train",
        input_lags=2,
        output_lags=2,
    )
    validate_result = validate_arx_rom(
        model_path=train_result.model_path,
        dataset_dir=validation_dataset_dir,
        out_dir=tmp_path / "validate",
    )

    assert train_result.train_cases == 2
    assert train_result.source_rows == 320
    assert train_result.fit_rows == 316
    assert train_result.model_path.exists()
    assert train_result.training_summary_path.exists()
    assert validate_result.case_count == 1
    assert validate_result.validation_rows == 158
    assert validate_result.metrics_path.exists()
    assert validate_result.prediction_csv_path.exists()
    metrics = json.loads(validate_result.metrics_path.read_text(encoding="utf-8"))
    assert metrics["training_performed"] is False
    assert metrics["case_ids"] == ["sparse24_seed_501"]
    assert set(metrics["metrics"]) == set(ROM_OUTPUT_COLUMNS)


def test_use_arx_rom_writes_checked_prediction_case(tmp_path):
    dataset_dir = tmp_path / "arx_dataset"
    generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=dataset_dir,
        count=1,
        start_seed=551,
    )
    train_result = train_arx_rom_from_dataset(
        dataset_dir=dataset_dir,
        out_dir=tmp_path / "model",
        input_lags=2,
        output_lags=2,
    )

    result = use_arx_rom_on_case(
        model_path=train_result.model_path,
        case_dir=dataset_dir / "sparse24_seed_551",
        out_dir=tmp_path / "prediction_case",
    )

    assert result.prediction_timeseries_path.exists()
    assert result.quality_report_path.exists()
    assert result.warmup_rows == 2
    assert result.predicted_rows == 158
    report = json.loads(result.quality_report_path.read_text(encoding="utf-8"))
    assert report["check_mode"] == "arx_use"
    assert report["run_success_flag"] is True


def test_use_arx_rom_on_schedule_expands_to_time_step(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    _write_minimal_case(case_dir, row_count=12)
    (case_dir / "config_summary.yaml").write_text(
        "time_step_seconds: 0.02\n",
        encoding="utf-8",
    )
    train_result = train_arx_rom_from_case(
        case_dir=case_dir,
        out_dir=tmp_path / "model",
        input_lags=1,
        output_lags=1,
    )

    result = use_arx_rom_on_schedule(
        model_path=train_result.model_path,
        schedule_path=case_dir / "actuation_schedule.csv",
        out_dir=tmp_path / "prediction_schedule",
    )

    with result.prediction_timeseries_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 56
    assert [int(float(row["window_id"])) for row in rows[:5]] == [0, 0, 0, 0, 0]
    assert float(rows[1]["physical_time"]) == 0.02
    manifest = json.loads(result.quality_report_path.read_text(encoding="utf-8"))
    assert manifest["check_mode"] == "arx_use"
    assert "time_step_source: config_summary" in (result.out_dir / "case_manifest.yaml").read_text(
        encoding="utf-8"
    )


def test_train_dataset_accepts_one_explicit_case_without_reserving_validation_data(tmp_path):
    dataset_dir = tmp_path / "one_case_dataset"
    generate_arx_sparse24_dataset(
        actuation_config_path="configs/actions/pilot_sparse24.yaml",
        mock_config_path="configs/mock_dynamic24x6.yaml",
        output_dir=dataset_dir,
        count=1,
        start_seed=601,
    )

    result = train_arx_rom_from_dataset(
        dataset_dir=dataset_dir,
        out_dir=tmp_path / "train",
        input_lags=2,
        output_lags=2,
    )

    assert result.train_cases == 1
    assert result.case_ids == ("sparse24_seed_601",)
    assert result.source_rows == 160
    assert result.fit_rows == 158


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
