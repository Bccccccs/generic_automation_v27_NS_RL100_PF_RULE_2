"""Mock plants for local sparse-jet flow-control workflow tests.

The :class:`MockPlant` class is a compact virtual CFD dynamical system for
RL/control validation. It intentionally has sparse delayed input coupling,
nonlinear effects, output inertia, and measurement noise so local control
loops can be exercised without STAR-CCM+.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

import numpy as np

from .data_schema import PlantObservation, Schedule


_HIDDEN_CRITICAL_JET_INDICES = (2, 6, 13, 17, 21)


@dataclass(frozen=True)
class MockPlantConfig:
    """Numerical parameters for the virtual 24-input, 6-output CFD plant."""

    n_inputs: int = 24
    n_states: int = 24
    n_outputs: int = 6
    delay_steps: int = 6
    delay_decay: float = 0.45
    output_beta: float = 0.28
    noise_std: float = 0.012
    disturbance_std: float = 0.004
    strong_connections_per_state: int = 3
    weak_connection_probability: float = 0.10
    hidden_gain: float = 3.5
    state_radius: float = 0.82
    input_gain: float = 0.18
    nonlinear_gain: float = 0.055
    quadratic_gain: float = 0.035
    state_clip: float = 8.0
    output_clip: float = 8.0

    def __post_init__(self) -> None:
        if self.n_inputs != 24:
            raise ValueError("MockPlant requires exactly 24 inputs")
        if self.n_outputs != 6:
            raise ValueError("MockPlant requires exactly 6 outputs")
        if self.n_states < self.n_inputs:
            raise ValueError("n_states must be at least 24 to include u(t) * u(t)")
        if not 3 <= self.delay_steps <= 10:
            raise ValueError("delay_steps K must be in [3, 10]")
        if not 0.0 < self.output_beta < 1.0:
            raise ValueError("output_beta must be in (0, 1)")
        if self.state_radius <= 0.0 or self.state_radius >= 1.0:
            raise ValueError("state_radius must be in (0, 1) for stable dynamics")


class MockPlant:
    """Virtual CFD plant with hidden high-impact jets.

    Public API intentionally exposes only ``reset(seed)`` and ``step(u, dt)``.
    The identity of the high-impact jets is encoded internally and is not
    returned by the plant.
    """

    def __init__(self, config: MockPlantConfig | None = None, seed: int | None = None):
        self.config = config or MockPlantConfig()
        self._rng = np.random.default_rng(seed)
        self._u_history: Deque[np.ndarray] = deque(maxlen=self.config.delay_steps + 1)
        self.t = 0
        self.A = np.zeros((self.config.n_states, self.config.n_states))
        self.B = np.zeros((self.config.n_states, self.config.n_inputs))
        self.C = np.zeros((self.config.n_outputs, self.config.n_states))
        self.W = np.zeros((self.config.n_states, self.config.n_inputs))
        self.noise_cov = np.zeros((self.config.n_outputs, self.config.n_outputs))
        self.x = np.zeros(self.config.n_states)
        self.y = np.zeros(self.config.n_outputs)
        self.reset(0 if seed is None else seed)

    def reset(self, seed: int):
        """Reset state, delay history, stochastic matrices, and RNG."""

        self._rng = np.random.default_rng(seed)
        self.A = self._build_stable_state_matrix()
        self.B = self._build_sparse_input_matrix()
        self.C = self._build_output_matrix()
        self.W = self._build_nonlinear_coupling()
        self.noise_cov = self._build_noise_covariance()
        self.x = np.zeros(self.config.n_states)
        self.y = np.zeros(self.config.n_outputs)
        self._u_history = deque(
            (np.zeros(self.config.n_inputs) for _ in range(self.config.delay_steps + 1)),
            maxlen=self.config.delay_steps + 1,
        )
        self.t = 0
        return self

    def step(self, u: np.ndarray, dt: float = 1.0):
        """Advance the plant by one control step and return ``y(t)``."""

        if dt <= 0.0:
            raise ValueError("dt must be positive")

        u_vec = np.asarray(u, dtype=float).reshape(-1)
        if u_vec.shape != (self.config.n_inputs,):
            raise ValueError(f"u must have shape ({self.config.n_inputs},)")

        u_vec = np.nan_to_num(u_vec, nan=0.0, posinf=2.0, neginf=-2.0)
        u_vec = np.clip(u_vec, -2.0, 2.0)
        self._u_history.appendleft(u_vec.copy())

        delayed_drive = np.zeros(self.config.n_states)
        for lag, lagged_u in enumerate(self._u_history):
            alpha = np.exp(-self.config.delay_decay * lag)
            delayed_drive += alpha * (self.B @ lagged_u)

        current_nonlinearity = self.config.nonlinear_gain * np.tanh(self.W @ u_vec)
        quadratic = np.zeros(self.config.n_states)
        quadratic[: self.config.n_inputs] = self.config.quadratic_gain * (u_vec * u_vec)
        disturbance = self._rng.normal(0.0, self.config.disturbance_std, self.config.n_states)

        self.x = (
            self.A @ self.x
            + self.config.input_gain * delayed_drive
            + current_nonlinearity
            + quadratic
            + disturbance
        )
        self.x = np.clip(self.x, -self.config.state_clip, self.config.state_clip)

        ideal_y = self.C @ self.x
        beta = 1.0 - (1.0 - self.config.output_beta) ** dt
        self.y = (1.0 - beta) * self.y + beta * ideal_y
        self.y = np.clip(self.y, -self.config.output_clip, self.config.output_clip)

        noise = self._rng.multivariate_normal(np.zeros(self.config.n_outputs), self.noise_cov)
        self.t += 1
        return self.y + noise

    def _build_stable_state_matrix(self) -> np.ndarray:
        raw = self._rng.normal(0.0, 0.16, (self.config.n_states, self.config.n_states))
        raw *= self._rng.random(raw.shape) < 0.18
        raw += np.eye(self.config.n_states) * 0.38
        radius = max(abs(np.linalg.eigvals(raw)))
        if radius == 0.0:
            return raw
        return raw * (self.config.state_radius / radius)

    def _build_sparse_input_matrix(self) -> np.ndarray:
        cfg = self.config
        matrix = np.zeros((cfg.n_states, cfg.n_inputs))
        hidden = set(_HIDDEN_CRITICAL_JET_INDICES)

        for state_idx in range(cfg.n_states):
            strong = self._rng.choice(
                cfg.n_inputs,
                size=min(cfg.strong_connections_per_state, cfg.n_inputs),
                replace=False,
            )
            for input_idx in range(cfg.n_inputs):
                if input_idx in strong:
                    scale = self._rng.uniform(0.65, 1.15)
                elif self._rng.random() < cfg.weak_connection_probability:
                    scale = self._rng.uniform(0.04, 0.22)
                else:
                    continue
                if input_idx in hidden:
                    scale *= cfg.hidden_gain
                matrix[state_idx, input_idx] = self._rng.choice((-1.0, 1.0)) * scale

        for input_idx in hidden:
            rows = self._rng.choice(cfg.n_states, size=max(4, cfg.n_states // 4), replace=False)
            signs = self._rng.choice((-1.0, 1.0), size=len(rows))
            matrix[rows, input_idx] += signs * self._rng.uniform(0.9, 1.6, size=len(rows))

        col_norm = np.linalg.norm(matrix, axis=0, keepdims=True)
        col_norm[col_norm == 0.0] = 1.0
        matrix = matrix / col_norm * np.sqrt(cfg.n_states)
        for input_idx in hidden:
            matrix[:, input_idx] *= cfg.hidden_gain
        return matrix

    def _build_output_matrix(self) -> np.ndarray:
        cfg = self.config
        matrix = self._rng.normal(0.0, 0.22, (cfg.n_outputs, cfg.n_states))
        matrix *= self._rng.random(matrix.shape) < 0.42
        for output_idx in range(cfg.n_outputs):
            matrix[output_idx, output_idx:: cfg.n_outputs] += self._rng.normal(
                0.38, 0.08, len(matrix[output_idx, output_idx:: cfg.n_outputs])
            )
        return matrix

    def _build_nonlinear_coupling(self) -> np.ndarray:
        cfg = self.config
        matrix = self._rng.normal(0.0, 0.34, (cfg.n_states, cfg.n_inputs))
        matrix *= self._rng.random(matrix.shape) < 0.22
        for input_idx in _HIDDEN_CRITICAL_JET_INDICES:
            matrix[:, input_idx] += self._rng.normal(0.0, 0.32, cfg.n_states)
        return matrix

    def _build_noise_covariance(self) -> np.ndarray:
        std = np.linspace(self.config.noise_std, self.config.noise_std * 1.8, self.config.n_outputs)
        corr = np.full((self.config.n_outputs, self.config.n_outputs), 0.18)
        np.fill_diagonal(corr, 1.0)
        return corr * np.outer(std, std)


def run_mock_plant(schedule: Schedule) -> list[PlantObservation]:
    """Simulate schedule execution with a simple monotonic response model."""

    observations: list[PlantObservation] = []
    drag = 1.0
    pressure_loss = 1.0

    for step in schedule.steps:
        active_mass_flow = sum(action.mass_flow_rate for action in step.actions if action.enabled)
        active_duty = sum(action.duty_cycle for action in step.actions if action.enabled)
        control_strength = min(active_mass_flow * 0.02 + active_duty * 0.01, 0.05)
        drag = max(0.2, drag * (1.0 - control_strength))
        pressure_loss = max(0.2, pressure_loss * (1.0 - control_strength * 0.5))
        observations.append(
            PlantObservation(
                iteration=step.iteration + step.duration_iterations,
                drag=drag,
                pressure_loss=pressure_loss,
                stable=control_strength < 0.049,
                notes="mock response",
            )
        )

    return observations
