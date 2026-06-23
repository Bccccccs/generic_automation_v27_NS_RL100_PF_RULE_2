from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from generic_automation.rl.rl_controller import ReinforcementLearningController
from generic_automation.core.runtime_value_utils import optional_text, safe_float, safe_int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay RL decisions from profiling timeseries/action logs without starting STAR-CCM+."
        )
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Project config YAML/JSON used to construct the Case and RL config.",
    )
    parser.add_argument(
        "--case-dir",
        required=True,
        help="Result directory containing profiling_timeseries.* and profiling_actions.jsonl.",
    )
    parser.add_argument(
        "--timeseries",
        default="",
        help="Override timeseries path. Defaults to <case-dir>/profiling_timeseries.jsonl, then .csv.",
    )
    parser.add_argument(
        "--actions",
        default="",
        help="Override actions path. Defaults to <case-dir>/profiling_actions.jsonl.",
    )
    parser.add_argument(
        "--mode",
        choices=("behavior", "policy"),
        default="behavior",
        help=(
            "behavior: train Q updates on recorded historical actions. "
            "policy: train on the replay controller's own predicted actions."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Replay only the first N action records for a quick smoke test.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "Directory for replay state/trace/output. "
            "Defaults to <case-dir>/_offline_replay/<timestamp>."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config).resolve()
    case_dir = Path(args.case_dir).resolve()
    if not case_dir.exists():
        raise FileNotFoundError(f"Case directory not found: {case_dir}")

    cfg, case, rl_config = load_case_and_rl_config(config_path)
    _ = cfg  # keep config load explicit for future extensions

    timeseries_path = resolve_timeseries_path(case_dir, args.timeseries)
    actions_path = resolve_actions_path(case_dir, args.actions)
    output_dir = resolve_output_dir(case_dir, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timeseries_rows = load_timeseries_rows(timeseries_path)
    action_rows = load_action_rows(actions_path)
    if args.limit > 0:
        action_rows = action_rows[: args.limit]
    if not action_rows:
        raise ValueError("No action rows available for replay.")

    controller = ReinforcementLearningController(
        rl_config=rl_config,
        case_dir=output_dir,
        case=case,
    )
    # Start each replay from a fresh state even when the output dir already exists.
    controller._q_table = {}
    controller._visit_counts = {}
    controller._adaptive_stage_baselines = {}
    controller._adaptive_stage_counts = {}
    controller._previous_state = None
    controller._previous_action = None
    controller._previous_action_applied = False
    controller._previous_observation = None
    controller._last_suggest_metadata = None

    comparison_path = output_dir / "replay_comparison.jsonl"
    summary_path = output_dir / "replay_summary.json"
    if comparison_path.exists():
        comparison_path.unlink()

    replay_rows, summary = run_replay(
        controller=controller,
        case=case,
        timeseries_rows=timeseries_rows,
        action_rows=action_rows,
        mode=args.mode,
    )

    with comparison_path.open("w", encoding="utf-8") as file_obj:
        for row in replay_rows:
            file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary.update(
        {
            "mode": args.mode,
            "config": str(config_path),
            "case_dir": str(case_dir),
            "timeseries_path": str(timeseries_path),
            "actions_path": str(actions_path),
            "output_dir": str(output_dir),
            "comparison_jsonl": comparison_path.name,
            "summary_json": summary_path.name,
            "controller_state_json": controller.STATE_FILE,
            "controller_trace_jsonl": controller.TRACE_FILE,
        }
    )
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def load_case_and_rl_config(config_path: Path) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    from generic_automation.core.project_config import load_config, parse_case

    cfg = load_config(config_path)
    case = parse_case(cfg)
    ai_cfg = cfg.get("ai_optimization", cfg.get("ai", {})) or {}
    rl_cfg = ai_cfg.get("reinforcement_learning", {}) or {}
    if not isinstance(rl_cfg, dict):
        raise ValueError("config.ai_optimization.reinforcement_learning must be a mapping")
    return cfg, case, rl_cfg


def resolve_timeseries_path(case_dir: Path, override: str) -> Path:
    if override:
        path = Path(override).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Timeseries file not found: {path}")
        return path

    for candidate in (
        case_dir / "profiling" / "profiling_timeseries.jsonl",
        case_dir / "profiling" / "profiling_timeseries.csv",
        case_dir / "profiling_timeseries.jsonl",
        case_dir / "profiling_timeseries.csv",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No profiling_timeseries.jsonl/csv found under {case_dir}"
    )


def resolve_actions_path(case_dir: Path, override: str) -> Path:
    if override:
        path = Path(override).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Actions file not found: {path}")
        return path
    for candidate in (
        case_dir / "profiling" / "profiling_actions.jsonl",
        case_dir / "profiling_actions.jsonl",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No profiling_actions.jsonl found under {case_dir}")



def resolve_output_dir(case_dir: Path, override: str) -> Path:
    if override:
        return Path(override).resolve()
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return (case_dir / "_offline_replay" / timestamp).resolve()


def load_timeseries_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file_obj:
            for line in file_obj:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    if path.suffix.lower() == ".csv":
        rows = []
        with path.open("r", encoding="utf-8", newline="") as file_obj:
            reader = csv.DictReader(file_obj)
            for row in reader:
                rows.append({key: parse_csv_cell(value) for key, value in row.items()})
        return rows

    raise ValueError(f"Unsupported timeseries format: {path}")


def parse_csv_cell(value: str | None) -> Any:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "nan"}:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    numeric = safe_float(text)
    if numeric is None:
        return text
    if "." not in text and "e" not in lowered:
        return int(round(numeric))
    return numeric


def load_action_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file_obj:
        for line in file_obj:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_replay_window_row(timeseries_row: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "iteration": safe_int(timeseries_row.get("iteration")) or 0,
        "wall_time_since_start": timeseries_row.get("wall_time_since_start_s"),
        "wall_time_per_chunk": timeseries_row.get("wall_time_per_chunk_s"),
        "cpu_hours_so_far": timeseries_row.get("cpu_hours_so_far"),
        "total_solver_cpu_time": timeseries_row.get("total_solver_cpu_time_s"),
        "max_residual": timeseries_row.get("max_residual"),
        "Continuity": timeseries_row.get("continuity_residual"),
        "X-momentum": timeseries_row.get("x_momentum_residual"),
        "Y-momentum": timeseries_row.get("y_momentum_residual"),
        "Z-momentum": timeseries_row.get("z_momentum_residual"),
        "Tke": timeseries_row.get("tke_residual"),
        "Sdr": timeseries_row.get("sdr_residual"),
        "Energy": timeseries_row.get("energy_residual"),
        "turbulent_viscosity_limited_cells": timeseries_row.get(
            "turbulent_viscosity_limited_cells"
        ),
    }

    drag_name = optional_text(timeseries_row.get("drag_metric_name"))
    if drag_name:
        row[drag_name] = timeseries_row.get("drag_latest")

    total_name = optional_text(timeseries_row.get("total_force_name"))
    if total_name:
        row[total_name] = timeseries_row.get("total_force_latest")

    pressure_name = optional_text(timeseries_row.get("pressure_metric_name"))
    if pressure_name:
        row[pressure_name] = timeseries_row.get("pressure_latest")

    return row


def run_replay(
    *,
    controller: ReinforcementLearningController,
    case: Any,
    timeseries_rows: list[dict[str, Any]],
    action_rows: list[dict[str, Any]],
    mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    replay_rows: list[dict[str, Any]] = []
    raw_window: list[dict[str, Any]] = []
    ts_index = 0
    reward_deltas: list[float] = []
    reward_sq_deltas: list[float] = []
    reward_compared = 0
    action_matches = 0

    previous_behavior_context: dict[str, Any] | None = None

    for idx, action_row in enumerate(action_rows):
        current_iteration = safe_int(action_row.get("iteration"))
        if current_iteration is None:
            continue

        while ts_index < len(timeseries_rows):
            ts_row = timeseries_rows[ts_index]
            ts_iteration = safe_int(ts_row.get("iteration"))
            if ts_iteration is None or ts_iteration > current_iteration:
                break
            raw_window.append(build_replay_window_row(ts_row))
            ts_index += 1

        if not raw_window:
            continue

        current_values = action_row.get("parameters_before")
        if not isinstance(current_values, dict):
            current_values = {}
        if not current_values:
            current_values = timeseries_rows[min(ts_index - 1, len(timeseries_rows) - 1)].get(
                "current_parameters",
                {},
            )
        if not isinstance(current_values, dict):
            current_values = {}

        if mode == "behavior" and previous_behavior_context is not None:
            controller._previous_state = previous_behavior_context["state"]
            controller._previous_action = previous_behavior_context["action"]
            controller._previous_observation = previous_behavior_context["observation"]
            controller._previous_action_applied = previous_behavior_context["applied"]

        proposal, metadata = controller.suggest(
            window=raw_window,
            current_values=current_values,
            constraints={},
            trigger_iteration=current_iteration,
        )
        _ = proposal

        predicted_action = metadata.get("action")
        recorded_action = action_row.get("action")
        action_match = predicted_action == recorded_action
        if action_match:
            action_matches += 1

        predicted_reward = None
        reward_info = metadata.get("reward")
        if isinstance(reward_info, dict):
            predicted_reward = safe_float(reward_info.get("value"))
        recorded_reward = safe_float(action_row.get("reward"))
        reward_delta = None
        if predicted_reward is not None and recorded_reward is not None:
            reward_delta = predicted_reward - recorded_reward
            reward_deltas.append(abs(reward_delta))
            reward_sq_deltas.append(reward_delta * reward_delta)
            reward_compared += 1

        replay_rows.append(
            {
                "index": idx,
                "iteration": current_iteration,
                "recorded_action": recorded_action,
                "predicted_action": predicted_action,
                "action_match": action_match,
                "recorded_reward": recorded_reward,
                "predicted_reward": predicted_reward,
                "reward_delta": reward_delta,
                "recorded_apply_success": bool(action_row.get("apply_success")),
                "recorded_blocked_reason": action_row.get("blocked_reason"),
                "decision_mode": metadata.get("decision_mode"),
                "state": metadata.get("state"),
            }
        )

        if mode == "policy":
            controller.mark_last_action_applied(bool(action_row.get("apply_success")))
        else:
            previous_behavior_context = {
                "state": metadata.get("state"),
                "action": recorded_action,
                "observation": metadata.get("observation"),
                "applied": bool(action_row.get("apply_success")),
            }

    action_count = len(replay_rows)
    summary = {
        "case_name": str(getattr(case, "case_name", "")),
        "action_rows_replayed": action_count,
        "action_match_count": action_matches,
        "action_match_rate": (
            action_matches / float(action_count) if action_count else 0.0
        ),
        "reward_rows_compared": reward_compared,
        "reward_mae": (
            sum(reward_deltas) / float(reward_compared) if reward_compared else None
        ),
        "reward_rmse": (
            math.sqrt(sum(reward_sq_deltas) / float(reward_compared))
            if reward_compared
            else None
        ),
        "first_iteration": replay_rows[0]["iteration"] if replay_rows else None,
        "last_iteration": replay_rows[-1]["iteration"] if replay_rows else None,
        "replay_rows_written": action_count,
    }
    return replay_rows, summary


if __name__ == "__main__":
    main()
