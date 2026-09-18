import copy
import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest
import yaml

from flow_control.rom.b54_real_rom import (
    RealROMDataError,
    load_real_arx_model,
    run_b54_real_rom,
)


REPRESENTATIVE_JETS = (2, 3, 9, 11, 17, 19)
INPUT_COLUMNS = tuple(f"actual_massflow_{jet:02d}" for jet in REPRESENTATIVE_JETS)
ALL_ACTUAL_COLUMNS = tuple(f"actual_massflow_{jet:02d}" for jet in range(1, 25))
OUTPUT_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")


def _pulse_inputs(order: tuple[int, ...], *, row_count: int = 300) -> np.ndarray:
    inputs = np.zeros((row_count, len(INPUT_COLUMNS)), dtype=float)
    cursor = 12
    for repetition, amplitude in enumerate((0.5, 1.0)):
        event_order = order if repetition == 0 else tuple(reversed(order))
        for jet in event_order:
            jet_index = REPRESENTATIVE_JETS.index(jet)
            inputs[cursor : cursor + 8, jet_index] = amplitude
            cursor += 20
    return inputs


def _response_from_inputs(inputs: np.ndarray) -> np.ndarray:
    """Create a stable six-output ARX response with a one-sample input delay."""

    response = np.zeros_like(inputs)
    autoregressive_gain = np.diag((0.72, 0.68, 0.74, 0.70, 0.66, 0.76))
    input_gain = np.asarray(
        [
            [42.0, 3.0, -1.0, 0.0, 0.0, 0.0],
            [2.0, -38.0, 2.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 35.0, -3.0, 0.0, 0.0],
            [0.0, 0.0, -2.0, 40.0, 2.0, 0.0],
            [0.0, 0.0, 0.0, 1.0, -32.0, 2.0],
            [1.0, 0.0, 0.0, 0.0, 3.0, 37.0],
        ],
        dtype=float,
    )
    for row_index in range(1, len(inputs)):
        response[row_index] = (
            autoregressive_gain @ response[row_index - 1]
            + input_gain @ inputs[row_index - 1]
        )
    return response


def _write_quality_report(case_dir: Path, inputs: np.ndarray) -> None:
    active_jets = [
        REPRESENTATIVE_JETS[index]
        for index in range(inputs.shape[1])
        if np.max(np.abs(inputs[:, index])) > 1.0e-8
    ]
    case_type = "no_jet" if not active_jets else "jet_on"
    payload = {
        "case_id": case_dir.name,
        "run_success_flag": True,
        "B04_real_quality": {
            "schema_version": "B04_real_data_quality_v1",
            "case_id": case_dir.name,
            "summary": {
                "overall_status": "PASS",
                "run_success_flag": True,
                "blocking_issue_count": 0,
            },
            "metrics": {
                "row_count": len(inputs),
                "active_jets": active_jets,
                "declared_case_type": case_type,
                "timeseries_sha256": _sha256(case_dir / "processed" / "timeseries.csv"),
                "case_manifest_sha256": _sha256(case_dir / "case_manifest.yaml"),
                "no_jet_physics": {
                    "drift_flags": [],
                    "jump_flags": [],
                    "asymmetry_flags": [],
                },
            },
        },
    }
    (case_dir / "quality_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_case(case_dir: Path, inputs: np.ndarray, delta_outputs: np.ndarray) -> Path:
    case_dir.mkdir(parents=True)
    processed_dir = case_dir / "processed"
    processed_dir.mkdir()
    baseline_levels = np.asarray((120.0, -80.0, 65.0, -45.0, 30.0, -15.0), dtype=float)
    fieldnames = ["physical_time", *ALL_ACTUAL_COLUMNS, *OUTPUT_COLUMNS]
    with (processed_dir / "timeseries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row_index in range(len(inputs)):
            writer.writerow(
                {
                    "physical_time": row_index * 0.01,
                    **{
                        column: (
                            float(inputs[row_index, INPUT_COLUMNS.index(column)])
                            if column in INPUT_COLUMNS
                            else 0.0
                        )
                        for column in ALL_ACTUAL_COLUMNS
                    },
                    **{
                        column: float(baseline_levels[column_index] + delta_outputs[row_index, column_index])
                        for column_index, column in enumerate(OUTPUT_COLUMNS)
                    },
                }
            )
    source_sim_path = case_dir / "source_result.sim"
    source_sim_path.write_bytes(
        np.column_stack((inputs, delta_outputs)).astype("<f8", copy=False).tobytes()
    )
    template_sim_path = case_dir / "template.sim"
    template_sim_path.write_bytes(b"b54-test-frozen-template-sim-v1")
    (case_dir / "case_manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": case_dir.name,
                "run_id": f"independent-run-{case_dir.name}",
                "case_type": "no_jet" if not np.any(inputs) else "jet_on",
                "source_run_evidence": {
                    "run_uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"b54-test/{case_dir.name}")),
                    "sequence_scope": "full_run",
                    "artifact_kind": "solver_result_sim",
                    "source_artifact_path": source_sim_path.name,
                    "source_artifact_sha256": _source_file_sha256(source_sim_path),
                },
                "rom_compatibility": {
                    "geometry_id": "test-geometry-v1",
                    "mesh_id": "test-mesh-v1",
                    "template_sim_path": template_sim_path.name,
                    "template_sim_sha256": _sha256(template_sim_path),
                    "flow_condition_id": "test-flow-v1",
                    "force_definition_id": "six-region-force-v1",
                    "force_unit": "N",
                    "massflow_unit": "kg/s",
                    "sign_convention_id": "test-positive-z-v1",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_quality_report(case_dir, inputs)
    return case_dir


def _write_three_independent_cases(root: Path) -> tuple[Path, Path, Path]:
    baseline_inputs = np.zeros((300, len(INPUT_COLUMNS)), dtype=float)
    baseline = _write_case(root / "baseline", baseline_inputs, np.zeros_like(baseline_inputs))

    training_inputs = _pulse_inputs((2, 3, 9, 11, 17, 19))
    training = _write_case(root / "training", training_inputs, _response_from_inputs(training_inputs))

    validation_inputs = _pulse_inputs((17, 11, 3, 19, 2, 9))
    validation = _write_case(
        root / "validation",
        validation_inputs,
        _response_from_inputs(validation_inputs),
    )
    return baseline, training, validation


def _write_config(
    root: Path,
    *,
    baseline: Path,
    training_cases: list[Path],
    validation_cases: list[Path],
    output_dir: Path,
) -> Path:
    config = {
        "project_root": str(root),
        "representative_jets": list(REPRESENTATIVE_JETS),
        "data": {
            "baseline_case": str(baseline),
            "training_cases": [str(path) for path in training_cases],
            "validation_cases": [str(path) for path in validation_cases],
        },
        "preprocessing": {"sample_stride": 1},
        "baseline": {"tail_fraction": 0.5},
        "model": {
            "input_lags": 2,
            "output_lags": 2,
            "ridge_alpha": 1.0e-6,
            "include_current_input": True,
        },
        "diagnostics": {
            "pre_event_s": 0.02,
            "response_horizon_s": 0.10,
        },
        "quality": {"require_b04_pass": True},
        "output_dir": str(output_dir),
    }
    config_path = root / "b54_real_rom.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return config_path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_file_sha256(path: Path) -> str:
    return _sha256(path)


def _refresh_quality_manifest_hash(case_dir: Path) -> None:
    quality_path = case_dir / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["metrics"]["case_manifest_sha256"] = _sha256(
        case_dir / "case_manifest.yaml"
    )
    quality_path.write_text(json.dumps(quality), encoding="utf-8")


def test_run_b54_real_rom_writes_independent_six_by_six_delivery(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )

    run_b54_real_rom(config_path)

    expected_artifacts = (
        "B04_real_ROM_model.json",
        "B04_real_ROM_metrics.json",
        "B04_real_ROM_result.md",
        "B04_real_ROM_training_prediction.png",
        "B04_real_ROM_validation_prediction.png",
        "B04_real_ROM_training_predictions.csv",
        "B04_real_ROM_validation_predictions.csv",
    )
    for filename in expected_artifacts:
        path = output_dir / filename
        assert path.is_file(), filename
        assert path.stat().st_size > 0, filename

    payload = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert payload["run_status"] == "COMPLETE"
    assert payload["acceptance_status"] == "REVIEW_REQUIRED"
    assert payload["data_contract"]["input_columns"] == list(INPUT_COLUMNS)
    assert payload["data_contract"]["output_columns"] == list(OUTPUT_COLUMNS)
    assert payload["reproducibility"]["config_file_sha256"] == _sha256(config_path)
    assert payload["reproducibility"]["effective_config_sha256"]
    assert payload["reproducibility"]["arx_module_sha256"] == _sha256(
        Path(__file__).parents[1] / "flow_control" / "rom" / "arx_model.py"
    )
    assert payload["reproducibility"]["quality_gate_module_sha256"] == _sha256(
        Path(__file__).parents[1] / "flow_control" / "star_ingest" / "b04_real_quality.py"
    )
    assert set(payload["reproducibility"]["runtime_versions"]) == {
        "python",
        "numpy",
        "pyyaml",
        "matplotlib",
    }
    assert payload["reproducibility"]["command_argv"][-2:] == [
        "--output-dir",
        str(output_dir),
    ]

    for split in ("training", "validation"):
        for mode in ("one_step", "rolling"):
            by_region = payload["metrics"][split][mode]
            assert set(by_region) == set(OUTPUT_COLUMNS)
            for region in OUTPUT_COLUMNS:
                values = by_region[region]
                assert {"rmse", "nrmse", "correlation"} <= set(values)
                assert math.isfinite(float(values["rmse"]))
                assert math.isfinite(float(values["nrmse"]))
                assert math.isfinite(float(values["correlation"]))
                assert -1.0 <= float(values["correlation"]) <= 1.0

    training_partition = payload["data_partition"]["training"]
    validation_partition = payload["data_partition"]["validation"]
    assert len(training_partition) == 1
    assert len(validation_partition) == 1
    training_hashes = {record["timeseries_sha256"] for record in training_partition}
    validation_hashes = {record["timeseries_sha256"] for record in validation_partition}
    assert training_hashes == {_sha256(training / "processed" / "timeseries.csv")}
    assert validation_hashes == {_sha256(validation / "processed" / "timeseries.csv")}
    assert training_hashes.isdisjoint(validation_hashes)
    assert training_partition[0]["quality_report_sha256"] == _sha256(
        training / "quality_report.json"
    )
    assert validation_partition[0]["quality_report_sha256"] == _sha256(
        validation / "quality_report.json"
    )
    result_markdown = (output_dir / "B04_real_ROM_result.md").read_text(encoding="utf-8")
    assert "独立验证连续滚动逐事件明细" in result_markdown
    assert all(column in result_markdown for column in INPUT_COLUMNS)

    validation_inputs = _pulse_inputs((17, 11, 3, 19, 2, 9))
    validation_truth = _response_from_inputs(validation_inputs)
    reloaded = load_real_arx_model(output_dir / "B04_real_ROM_model.json")
    reloaded_one_step = reloaded.predict_one_step(validation_inputs, validation_truth)
    reloaded_rolling = reloaded.predict_rolling(validation_inputs, validation_truth)
    with (output_dir / "B04_real_ROM_validation_predictions.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        prediction_rows = list(csv.DictReader(handle))
    start = reloaded.arx.max_lag
    csv_one_step = np.asarray(
        [
            [float(row[f"delta_{region}_one_step"]) for region in OUTPUT_COLUMNS]
            for row in prediction_rows[start:]
        ]
    )
    csv_rolling = np.asarray(
        [
            [float(row[f"delta_{region}_rolling"]) for region in OUTPUT_COLUMNS]
            for row in prediction_rows[start:]
        ]
    )
    np.testing.assert_allclose(reloaded_one_step[start:], csv_one_step, rtol=0.0, atol=1.0e-12)
    np.testing.assert_allclose(reloaded_rolling[start:], csv_rolling, rtol=0.0, atol=1.0e-12)

    polluted_future_truth = validation_truth.copy()
    polluted_future_truth[start:] += np.linspace(
        1.0e3,
        2.0e3,
        len(polluted_future_truth) - start,
    )[:, None]
    polluted_rolling = reloaded.predict_rolling(validation_inputs, polluted_future_truth)
    np.testing.assert_allclose(
        reloaded_rolling[start:],
        polluted_rolling[start:],
        rtol=0.0,
        atol=0.0,
    )


def test_load_real_arx_model_rejects_corrupt_or_broadcastable_snapshots(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )
    run_b54_real_rom(config_path)
    model_path = output_dir / "B04_real_ROM_model.json"
    valid = json.loads(model_path.read_text(encoding="utf-8"))

    corrupt_payloads: list[tuple[str, dict[str, object]]] = []
    wrong_coefficients = copy.deepcopy(valid)
    wrong_coefficients["arx"]["coefficients"] = [
        [row[0]] for row in wrong_coefficients["arx"]["coefficients"]
    ]
    corrupt_payloads.append(("coefficient shape", wrong_coefficients))

    nonfinite_coefficients = copy.deepcopy(valid)
    nonfinite_coefficients["arx"]["coefficients"][0][0] = float("nan")
    corrupt_payloads.append(("non-finite coefficient", nonfinite_coefficients))

    wrong_scaler_shape = copy.deepcopy(valid)
    wrong_scaler_shape["scaling"]["input_mean"] = [0.0]
    corrupt_payloads.append(("scaler shape", wrong_scaler_shape))

    nonfinite_scaler = copy.deepcopy(valid)
    nonfinite_scaler["scaling"]["output_mean"][0] = float("inf")
    corrupt_payloads.append(("non-finite scaler", nonfinite_scaler))

    nonpositive_scale = copy.deepcopy(valid)
    nonpositive_scale["scaling"]["output_scale"][0] = 0.0
    corrupt_payloads.append(("non-positive scale", nonpositive_scale))

    wrong_input_names = copy.deepcopy(valid)
    wrong_input_names["arx"]["input_names"][0] = "cmd_massflow_02"
    corrupt_payloads.append(("input names", wrong_input_names))

    mismatched_contract = copy.deepcopy(valid)
    mismatched_contract["data_contract"]["input_columns"][0] = "actual_massflow_01"
    corrupt_payloads.append(("data contract", mismatched_contract))

    wrong_names = copy.deepcopy(valid)
    wrong_names["arx"]["output_names"][0] = "delta_wrong_region"
    corrupt_payloads.append(("output names", wrong_names))

    wrong_feature_names = copy.deepcopy(valid)
    wrong_feature_names["arx"]["feature_names"][0] = "wrong_intercept"
    corrupt_payloads.append(("feature names", wrong_feature_names))

    wrong_model_type = copy.deepcopy(valid)
    wrong_model_type["model_type"] = "other"
    corrupt_payloads.append(("model type", wrong_model_type))

    wrong_fit_source = copy.deepcopy(valid)
    wrong_fit_source["scaling"]["fit_source"] = "all cases"
    corrupt_payloads.append(("fit source", wrong_fit_source))

    coerced_boolean = copy.deepcopy(valid)
    coerced_boolean["arx"]["include_current_input"] = "false"
    corrupt_payloads.append(("coerced boolean", coerced_boolean))

    coerced_lag = copy.deepcopy(valid)
    coerced_lag["arx"]["input_lags"] = "2"
    corrupt_payloads.append(("coerced lag", coerced_lag))

    for index, (label, payload) in enumerate(corrupt_payloads):
        corrupt_path = tmp_path / f"corrupt_{index}_{label.replace(' ', '_')}.json"
        corrupt_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError):
            load_real_arx_model(corrupt_path)

    model = load_real_arx_model(model_path)
    with pytest.raises(ValueError, match="six-column"):
        model.predict_one_step(np.zeros((20, 1)), np.zeros((20, 6)))
    with pytest.raises(ValueError, match="six-column"):
        model.predict_rolling(np.zeros((20, 6)), np.zeros((20, 1)))
    with pytest.raises(ValueError, match="same row count"):
        model.predict_one_step(np.zeros((20, 6)), np.zeros((21, 6)))


@pytest.mark.parametrize("duplicate_kind", ("same_path", "same_hash"))
def test_run_b54_real_rom_rejects_training_validation_overlap(
    tmp_path: Path,
    duplicate_kind: str,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    if duplicate_kind == "same_path":
        validation = training
    else:
        shutil.rmtree(validation)
        shutil.copytree(training, validation)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_empty_actual_massflow_value(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    timeseries_path = training / "processed" / "timeseries.csv"
    with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[40]["actual_massflow_02"] = ""
    with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_requires_nested_b04_quality_gate(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    (training / "quality_report.json").write_text(
        json.dumps({"run_success_flag": True}),
        encoding="utf-8",
    )
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="B04_real_quality"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_requires_top_level_and_nested_quality_pass(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    quality_path = validation / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["run_success_flag"] = False
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="quality gate is not PASS"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_bad_case_yaml_replaces_prior_success_with_blocked(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )
    run_b54_real_rom(config_path)
    (validation / "case_manifest.yaml").write_text(
        "case_id: [unterminated\n",
        encoding="utf-8",
    )

    with pytest.raises(RealROMDataError):
        run_b54_real_rom(config_path)
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert not (output_dir / "B04_real_ROM_model.json").exists()
    assert list((output_dir / "B04_real_ROM_stale").glob("B04_real_ROM_model_*.json"))


def test_run_b54_real_rom_missing_config_with_output_override_is_blocked(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "delivery"
    output_dir.mkdir()
    (output_dir / "B04_real_ROM_model.json").write_text("{}", encoding="utf-8")
    (output_dir / "B04_real_ROM_metrics.json").write_text(
        json.dumps({"status": "COMPLETE"}),
        encoding="utf-8",
    )
    (output_dir / "B04_real_ROM_result.md").write_text("old success", encoding="utf-8")

    with pytest.raises(RealROMDataError, match="configuration file"):
        run_b54_real_rom(tmp_path / "missing.yaml", output_dir=output_dir)
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert blocked["invalid_configuration"] is True
    assert blocked["reproducibility"]["config_file_exists"] is False
    assert not (output_dir / "B04_real_ROM_model.json").exists()


@pytest.mark.parametrize("failure_kind", ("missing", "malformed"))
def test_run_b54_real_rom_bad_config_without_override_invalidates_default_delivery(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    config_path = tmp_path / "configs" / "b54" / "real_rom.yaml"
    config_path.parent.mkdir(parents=True)
    if failure_kind == "malformed":
        config_path.write_text("project_root: [unterminated\n", encoding="utf-8")
    output_dir = tmp_path / "artifacts"
    output_dir.mkdir()
    (output_dir / "B04_real_ROM_model.json").write_text("{}", encoding="utf-8")
    (output_dir / "B04_real_ROM_metrics.json").write_text(
        json.dumps({"status": "COMPLETE"}),
        encoding="utf-8",
    )
    (output_dir / "B04_real_ROM_result.md").write_text("old success", encoding="utf-8")

    with pytest.raises(RealROMDataError, match="configuration file"):
        run_b54_real_rom(config_path)
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert blocked["invalid_configuration"] is True
    assert not (output_dir / "B04_real_ROM_model.json").exists()
    assert list((output_dir / "B04_real_ROM_stale").glob("B04_real_ROM_model_*.json"))


def test_run_b54_real_rom_invalid_project_root_honors_absolute_output_override(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "invalid_project_root.yaml"
    config_path.write_text("project_root: []\n", encoding="utf-8")
    output_dir = tmp_path / "explicit_delivery"
    output_dir.mkdir()
    (output_dir / "B04_real_ROM_model.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RealROMDataError, match="invalid B54 configuration"):
        run_b54_real_rom(config_path, output_dir=output_dir)
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert blocked["invalid_configuration"] is True
    assert not (output_dir / "B04_real_ROM_model.json").exists()
    assert list((output_dir / "B04_real_ROM_stale").glob("B04_real_ROM_model_*.json"))


def test_b54_wrapper_default_config_is_anchored_outside_repository(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    script_path = repository_root / "scripts" / "analysis" / "run_b54_real_rom.py"
    output_dir = tmp_path / "delivery"
    completed = subprocess.run(
        [sys.executable, str(script_path), "--output-dir", str(output_dir)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert "invalid_configuration" not in blocked
    assert blocked["reproducibility"]["config_path"] == str(
        repository_root / "configs" / "b54" / "real_rom.yaml"
    )


def test_run_b54_real_rom_forbids_quality_gate_bypass(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )
    run_b54_real_rom(config_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["quality"]["require_b04_pass"] = False
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(RealROMDataError, match="must be true"):
        run_b54_real_rom(config_path)
    output_dir = tmp_path / "delivery"
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert not (output_dir / "B04_real_ROM_model.json").exists()
    assert list((output_dir / "B04_real_ROM_stale").glob("B04_real_ROM_model_*.json"))


@pytest.mark.parametrize("invalid_jet", (2.9, "2", True))
def test_run_b54_real_rom_rejects_noninteger_representative_jet_ids(
    tmp_path: Path,
    invalid_jet: object,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )
    run_b54_real_rom(config_path)
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["representative_jets"][0] = invalid_jet
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    with pytest.raises(RealROMDataError, match="YAML integers"):
        run_b54_real_rom(config_path)
    output_dir = tmp_path / "delivery"
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert blocked["invalid_configuration"] is True
    assert not (output_dir / "B04_real_ROM_model.json").exists()


def test_run_b54_real_rom_rejects_extreme_finite_values_fail_closed(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    for case_dir in (training, validation):
        timeseries_path = case_dir / "processed" / "timeseries.csv"
        with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
            fieldnames = list(rows[0])
        for row in rows:
            for column in INPUT_COLUMNS:
                if float(row[column]) != 0.0:
                    row[column] = str(float(row[column]) * 1.0e200)
        with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        quality_path = case_dir / "quality_report.json"
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        quality["B04_real_quality"]["metrics"]["timeseries_sha256"] = _sha256(
            timeseries_path
        )
        quality_path.write_text(json.dumps(quality), encoding="utf-8")
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )

    with pytest.raises(RealROMDataError, match="numeric safety limit"):
        run_b54_real_rom(config_path)
    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert not (output_dir / "B04_real_ROM_model.json").exists()


def test_run_b54_real_rom_rejects_shared_source_run_provenance(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    validation_manifest = yaml.safe_load(
        (validation / "case_manifest.yaml").read_text(encoding="utf-8")
    )
    validation_manifest["run_id"] = "independent-run-training"
    (validation / "case_manifest.yaml").write_text(
        yaml.safe_dump(validation_manifest, sort_keys=False),
        encoding="utf-8",
    )
    _refresh_quality_manifest_hash(validation)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="source run provenance"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_requires_verifiable_complete_source_evidence(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    manifest_path = validation / "case_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("source_run_evidence")
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _refresh_quality_manifest_hash(validation)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="complete upstream run"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_reused_source_artifact_with_different_run_ids(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    training_source = training / "source_result.sim"
    validation_source = validation / "source_result.sim"
    shutil.copyfile(training_source, validation_source)
    manifest_path = validation / "case_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["run_id"] = "rewritten-validation-id"
    manifest["source_run_evidence"]["run_uuid"] = str(uuid.uuid4())
    manifest["source_run_evidence"]["source_artifact_sha256"] = _source_file_sha256(
        validation_source
    )
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _refresh_quality_manifest_hash(validation)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="source run provenance"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_duplicate_cases_within_training_split(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training, training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="training contains duplicate"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_baseline_validation_shared_provenance(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    baseline_manifest_path = baseline / "case_manifest.yaml"
    baseline_manifest = yaml.safe_load(baseline_manifest_path.read_text(encoding="utf-8"))
    baseline_manifest["run_id"] = "independent-run-validation"
    baseline_manifest_path.write_text(
        yaml.safe_dump(baseline_manifest, sort_keys=False),
        encoding="utf-8",
    )
    _refresh_quality_manifest_hash(baseline)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="baseline and validation.*source run provenance"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_physical_compatibility_mismatch(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    manifest_path = validation / "case_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["rom_compatibility"]["mesh_id"] = "different-mesh"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _refresh_quality_manifest_hash(validation)
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="physical compatibility signatures differ"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_composite_events_for_per_jet_diagnostics(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    timeseries_path = validation / "processed" / "timeseries.csv"
    with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    for row in rows:
        if float(row["actual_massflow_02"]) != 0.0:
            row["actual_massflow_03"] = row["actual_massflow_02"]
    with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    quality_path = validation / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["metrics"]["timeseries_sha256"] = _sha256(timeseries_path)
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="no isolated.*actual_massflow_02"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_validation_without_detectable_truth_response(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    timeseries_path = validation / "processed" / "timeseries.csv"
    with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    constant_levels = {
        "Fz_S1L": 120.0,
        "Fz_S1R": -80.0,
        "Fz_S2L": 65.0,
        "Fz_S2R": -45.0,
        "Fz_S3L": 30.0,
        "Fz_S3R": -15.0,
    }
    for row in rows:
        row.update(constant_levels)
    with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    quality_path = validation / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["metrics"]["timeseries_sha256"] = _sha256(timeseries_path)
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="no isolated event with a detectable truth response"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_rejects_stale_quality_and_quarantines_success_artifacts(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )
    run_b54_real_rom(config_path)

    timeseries_path = validation / "processed" / "timeseries.csv"
    with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[100]["Fz_S1L"] = str(float(rows[100]["Fz_S1L"]) + 1.0e6)
    with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(RealROMDataError, match="quality report is missing or stale"):
        run_b54_real_rom(config_path)

    blocked = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "BLOCKED"
    assert not (output_dir / "B04_real_ROM_model.json").exists()
    assert list((output_dir / "B04_real_ROM_stale").glob("B04_real_ROM_model_*.json"))


def test_run_b54_real_rom_checks_nonrepresentative_flow_in_nojet_baseline(tmp_path: Path) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    timeseries_path = baseline / "processed" / "timeseries.csv"
    with timeseries_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    for row in rows:
        row["actual_massflow_01"] = 0.2
    with timeseries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    quality_path = baseline / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["metrics"]["active_jets"] = [1]
    quality["B04_real_quality"]["metrics"]["timeseries_sha256"] = _sha256(timeseries_path)
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=tmp_path / "delivery",
    )

    with pytest.raises(RealROMDataError, match="not no-jet"):
        run_b54_real_rom(config_path)


def test_run_b54_real_rom_requires_review_for_baseline_drift_or_jump_flags(
    tmp_path: Path,
) -> None:
    baseline, training, validation = _write_three_independent_cases(tmp_path)
    quality_path = baseline / "quality_report.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["B04_real_quality"]["metrics"]["no_jet_physics"]["drift_flags"] = [
        {"series": "Fz_S1L", "relative_drift": 0.2}
    ]
    quality_path.write_text(json.dumps(quality), encoding="utf-8")
    output_dir = tmp_path / "delivery"
    config_path = _write_config(
        tmp_path,
        baseline=baseline,
        training_cases=[training],
        validation_cases=[validation],
        output_dir=output_dir,
    )

    with pytest.raises(RealROMDataError, match="unresolved B04 no-jet drift/jump"):
        run_b54_real_rom(config_path)

    manifest_path = baseline / "case_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["b54_baseline_review_approved"] = True
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    _refresh_quality_manifest_hash(baseline)
    run_b54_real_rom(config_path)
    metrics = json.loads((output_dir / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    review = metrics["baseline"]["B04_nojet_stability_review"]
    assert review["drift_flag_count"] == 1
    assert review["explicit_review_approved"] is True
