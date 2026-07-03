"""Mock-plant rollout and analysis for local flow-control validation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .mock_plant import MockPlant, MockPlantConfig


@dataclass(frozen=True)
class MockRolloutResult:
    outputs: np.ndarray
    stability: dict[str, object]
    correlations: list[dict[str, object]]
    ranking: list[dict[str, object]]


def run_mock_rollout(
    inputs: np.ndarray,
    *,
    plant_config: MockPlantConfig,
    plant_seed: int,
    window_duration: float,
    command_amplitude: float,
) -> MockRolloutResult:
    plant = MockPlant(plant_config).reset(plant_seed)
    outputs = run_plant(plant, inputs, window_duration)
    return MockRolloutResult(
        outputs=outputs,
        stability=stability_check(outputs),
        correlations=input_output_correlations(inputs, outputs, max_lag=plant_config.delay_steps),
        ranking=influence_ranking(
            plant_config,
            plant_seed,
            horizon=40,
            amplitude=command_amplitude,
        ),
    )


def run_plant(plant: MockPlant, inputs: np.ndarray, dt: float) -> np.ndarray:
    outputs = []
    for step_idx in range(inputs.shape[1]):
        outputs.append(plant.step(inputs[:, step_idx], dt=dt))
    return np.asarray(outputs, dtype=float).T


def stability_check(outputs: np.ndarray) -> dict[str, object]:
    finite = bool(np.all(np.isfinite(outputs)))
    max_abs = float(np.max(np.abs(outputs))) if outputs.size else 0.0
    rms_tail = float(np.sqrt(np.mean(outputs[:, -10:] ** 2))) if outputs.shape[1] >= 10 else max_abs
    return {
        "stable": bool(finite and max_abs < 20.0 and rms_tail < 10.0),
        "finite": finite,
        "max_abs_y": max_abs,
        "tail_rms_y": rms_tail,
        "divergence_threshold": 20.0,
    }


def input_output_correlations(
    inputs: np.ndarray, outputs: np.ndarray, max_lag: int
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for input_idx in range(inputs.shape[0]):
        for output_idx in range(outputs.shape[0]):
            best_lag = 0
            best_corr = 0.0
            for lag in range(max_lag + 1):
                x = inputs[input_idx, : inputs.shape[1] - lag if lag else inputs.shape[1]]
                y = outputs[output_idx, lag:]
                corr = safe_corr(x, y)
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag
            rows.append(
                {
                    "jet_id": f"J{input_idx + 1:02d}",
                    "output_id": f"Y{output_idx + 1}",
                    "best_lag": best_lag,
                    "correlation": best_corr,
                    "abs_correlation": abs(best_corr),
                }
            )
    return rows


def safe_corr(left: np.ndarray, right: np.ndarray) -> float:
    if left.size < 2 or right.size < 2 or np.std(left) == 0.0 or np.std(right) == 0.0:
        return 0.0
    return float(np.corrcoef(left, right)[0, 1])


def influence_ranking(
    config: MockPlantConfig, seed: int, horizon: int, amplitude: float
) -> list[dict[str, object]]:
    scores: list[dict[str, object]] = []
    baseline = impulse_response(config, seed, None, horizon, amplitude)
    for input_idx in range(config.n_inputs):
        response = impulse_response(config, seed, input_idx, horizon, amplitude)
        delta = response - baseline
        score = float(np.sqrt(np.sum(delta * delta)))
        peak = float(np.max(np.linalg.norm(delta, axis=0)))
        scores.append({"jet_id": f"J{input_idx + 1:02d}", "score": score, "peak_delta": peak})
    scores.sort(key=lambda item: item["score"], reverse=True)
    for rank, item in enumerate(scores, start=1):
        item["rank"] = rank
    return scores


def impulse_response(
    config: MockPlantConfig, seed: int, input_idx: int | None, horizon: int, amplitude: float
) -> np.ndarray:
    plant = MockPlant(config).reset(seed)
    rows = []
    for step_idx in range(horizon):
        u = np.zeros(config.n_inputs)
        if input_idx is not None and step_idx == 0:
            u[input_idx] = amplitude
        rows.append(plant.step(u))
    return np.asarray(rows, dtype=float).T

