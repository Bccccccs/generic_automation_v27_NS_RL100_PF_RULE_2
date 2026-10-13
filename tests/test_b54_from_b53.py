from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from flow_control.rom.b54_from_b53 import (
    REQUIRED_COLUMNS,
    ROMDataError,
    load_config,
    read_dataset,
    run,
)
from flow_control.rom.b54_real_rom import load_real_arx_model

JETS = (2, 3, 9, 11, 17, 19)
REGIONS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
DT = 1.0e-3


def _simulate(seed: int, pulses: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple[str, float]]]:
    """已知一阶 ARX 系统：y[t] = 0.6 y[t-1] + B u[t-1]，喷口 i 主要作用于镜像区域。"""

    rng = np.random.default_rng(seed)
    gain = np.full((6, 6), 20.0)
    for jet in range(6):
        gain[jet, jet ^ 1] = 400.0
    period, on = 200, 50
    steps = 100 + pulses * period
    u = np.zeros((steps, 6))
    events: list[tuple[str, float]] = []
    for pulse in range(pulses):
        jet = int(rng.integers(6)) if pulse >= 6 else pulse
        start = 100 + pulse * period
        u[start : start + on, jet] = float(rng.choice([1.43, 2.86]))
        events.append((f"J{JETS[jet]:02d}", start * DT))
    y = np.zeros((steps, 6))
    for t in range(1, steps):
        y[t] = 0.6 * y[t - 1] + u[t - 1] @ gain + rng.normal(0.0, 2.0, 6)
    return np.arange(1, steps + 1) * DT, u, y, events


def _write_dataset(path: Path, case_id: str, seed: int, pulses: int, *, mode: str = "reference_time_aligned") -> None:
    time, u, y, events = _simulate(seed, pulses)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REQUIRED_COLUMNS))
        writer.writeheader()
        for index, time_value in enumerate(time):
            owner = max((event for event in events if event[1] - 0.05 < time_value), key=lambda event: event[1], default=events[0])
            phase = "baseline" if time_value <= owner[1] else "actuation"
            row: dict[str, object] = {
                "source_case_id": case_id,
                "segment_id": f"{case_id}__seg000",
                "physical_time": f"{time_value:.6f}",
                "event_id": f"{case_id}__{owner[0]}__{owner[1]:.3f}",
                "jet_id": owner[0],
                "phase": phase,
                "action_start_s": owner[1],
                "baseline_mode": mode,
            }
            for jet in range(1, 25):
                row[f"actual_massflow_{jet:02d}"] = u[index, JETS.index(jet)] if jet in JETS else 0.0
            for position, region in enumerate(REGIONS):
                row[f"delta_{region}"] = y[index, position]
                row[f"baseline_noise_{region}"] = 2.0
            writer.writerow(row)


def _config(tmp_path: Path, training: Path, validation: Path) -> dict:
    path = tmp_path / "configs" / "b54" / "rom.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                f"project_root: {tmp_path}",
                "data:",
                f"  training_dataset: {training}",
                f"  validation_dataset: {validation}",
                "preprocessing: {sample_stride: 1}",
                "model: {input_history_s: [0.003], output_lags: [1, 2], ridge_alpha: [0.01], cv_folds: 3}",
                "diagnostics: {response_horizon_s: 0.05}",
                f"output_dir: {tmp_path / 'out'}",
            ]
        ),
        encoding="utf-8",
    )
    return load_config(path)


def test_rom_recovers_known_system_on_independent_validation(tmp_path: Path) -> None:
    training = tmp_path / "training_dataset.csv"
    validation = tmp_path / "validation_dataset.csv"
    _write_dataset(training, "train_case", seed=1, pulses=24)
    _write_dataset(validation, "val_case", seed=2, pulses=12)

    metrics = run(_config(tmp_path, training, validation))

    assert metrics["run_status"] == "COMPLETE"
    rolling = metrics["metrics"]["validation"]["rolling"]["by_region"]
    assert all(rolling[region]["correlation"] > 0.99 for region in REGIONS)
    by_jet = {row["jet_id"]: row for row in metrics["response_by_jet"]["validation"]}
    assert by_jet["J03"]["truth_dominant_region"] == "Fz_S1L"
    assert all(row["dominant_region_match_rate"] == 1.0 for row in by_jet.values() if row["evaluable_events"])
    model = load_real_arx_model(tmp_path / "out" / "B04_real_ROM_model.json")
    assert model.arx.input_names_ == [f"actual_massflow_{jet:02d}" for jet in JETS]
    stored = json.loads((tmp_path / "out" / "B04_real_ROM_metrics.json").read_text(encoding="utf-8"))
    assert stored["data"]["validation"]["case_ids"] == ["val_case"]
    assert (tmp_path / "out" / "B04_real_ROM_validation_prediction.png").is_file()


def test_missing_validation_is_reported_as_training_only(tmp_path: Path) -> None:
    training = tmp_path / "training_dataset.csv"
    _write_dataset(training, "train_case", seed=1, pulses=24)

    metrics = run(_config(tmp_path, training, tmp_path / "absent.csv"))

    assert metrics["run_status"] == "TRAINING_ONLY"
    assert metrics["acceptance_status"] == "NOT_EVALUATED"
    assert "validation" not in metrics["metrics"]
    assert "未执行" in (tmp_path / "out" / "B04_real_ROM_result.md").read_text(encoding="utf-8")


def test_same_case_in_training_and_validation_is_refused(tmp_path: Path) -> None:
    training = tmp_path / "training_dataset.csv"
    validation = tmp_path / "validation_dataset.csv"
    _write_dataset(training, "shared_case", seed=1, pulses=24)
    _write_dataset(validation, "shared_case", seed=2, pulses=12)

    with pytest.raises(ROMDataError, match="同时出现在训练和验证"):
        run(_config(tmp_path, training, validation))


def test_local_baseline_dataset_is_refused(tmp_path: Path) -> None:
    training = tmp_path / "training_dataset.csv"
    _write_dataset(training, "train_case", seed=1, pulses=8, mode="local_pre_jet_median")

    with pytest.raises(ROMDataError, match="baseline_mode"):
        read_dataset(training, JETS)
