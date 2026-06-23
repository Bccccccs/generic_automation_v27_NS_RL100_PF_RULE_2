#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from generic_automation.monitor.ai_case_modifier import AICaseModifier
from generic_automation.core.project_config import load_config, parse_case, resolve_case_dir


def _parse_kv(text: str) -> tuple[str, float]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got: {text!r}")
    key, raw = text.split("=", 1)
    key = key.strip()
    raw = raw.strip()
    if not key:
        raise argparse.ArgumentTypeError(f"empty key in: {text!r}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"value for {key!r} must be numeric, got: {raw!r}"
        ) from exc
    return key, value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force-write online STAR-CCM+ parameter updates into param_update.json."
    )
    parser.add_argument(
        "--config",
        default="configs/config.yaml",
        help="Path to config file",
    )
    parser.add_argument(
        "--case-dir",
        default=None,
        help="Override case_dir path (default: resolved from config)",
    )
    parser.add_argument(
        "--pressure-relaxation-factor",
        type=float,
        default=None,
        help="Force-write pressure_relaxation_factor",
    )
    parser.add_argument(
        "--pressure-relaxation-initial-value",
        type=float,
        default=None,
        help="Force-write pressure_relaxation_initial_value",
    )
    parser.add_argument(
        "--pressure-relaxation-start-iteration",
        type=int,
        default=None,
        help="Force-write pressure_relaxation_start_iteration",
    )
    parser.add_argument(
        "--pressure-relaxation-end-iteration",
        type=int,
        default=None,
        help="Force-write pressure_relaxation_end_iteration",
    )
    parser.add_argument(
        "--velocity-relaxation-initial-value",
        type=float,
        default=None,
        help="Force-write velocity_relaxation_initial_value",
    )
    parser.add_argument(
        "--velocity-relaxation-start-iteration",
        type=int,
        default=None,
        help="Force-write velocity_relaxation_start_iteration",
    )
    parser.add_argument(
        "--velocity-relaxation-end-iteration",
        type=int,
        default=None,
        help="Force-write velocity_relaxation_end_iteration",
    )
    parser.add_argument(
        "--amg-solver",
        type=int,
        choices=(0, 1),
        default=None,
        help="Force-write legacy amg_solver as 0 or 1",
    )
    parser.add_argument(
        "--amg-cycle",
        type=int,
        choices=(0, 1),
        default=None,
        help="Force-write legacy amg_cycle alias as 0 or 1",
    )
    parser.add_argument(
        "--pressure-amg-cycle",
        type=int,
        choices=(0, 1),
        default=None,
        help="Force-write pressure_amg_cycle as 0 (V-cycle) or 1 (W-cycle)",
    )
    parser.add_argument(
        "--velocity-amg-cycle",
        type=int,
        choices=(0, 1),
        default=None,
        help="Force-write velocity_amg_cycle as 0 (Flex-cycle) or 1 (V-cycle)",
    )
    parser.add_argument(
        "--pressure-amg-max-cycles",
        type=int,
        default=None,
        help="Force-write pressure_amg_max_cycles",
    )
    parser.add_argument(
        "--pressure-amg-converge-tol",
        type=float,
        default=None,
        help="Force-write pressure_amg_converge_tol",
    )
    parser.add_argument(
        "--pressure-amg-epsilon",
        type=float,
        default=None,
        help="Force-write pressure_amg_epsilon",
    )
    parser.add_argument(
        "--set",
        dest="kv_pairs",
        action="append",
        type=_parse_kv,
        default=[],
        help="Additional KEY=VALUE entries, for example --set convergence_residual=1e-3",
    )
    parser.add_argument(
        "--trigger-iteration",
        type=int,
        default=-1,
        help="Optional marker written into ai_update_history.jsonl",
    )
    parser.add_argument(
        "--show-only",
        action="store_true",
        help="Resolve and print the target case_dir without writing anything",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = load_config(config_path)
    case = parse_case(cfg)
    case_dir = (
        Path(args.case_dir).resolve()
        if args.case_dir
        else resolve_case_dir(config_path, cfg, case.case_name)
    )
    case_dir_exists = case_dir.exists()

    print(f"[ForceUpdate] config:   {config_path}")
    print(f"[ForceUpdate] case_dir: {case_dir}")
    if not case_dir_exists:
        print(
            "[ForceUpdate] WARNING: case_dir does not exist yet; it will be created for the "
            "handshake files. STAR-CCM+ will only consume the update if it is running against "
            "this same case_dir.",
            file=sys.stderr,
        )

    if args.show_only:
        return 0

    params: dict[str, Any] = {}
    if args.pressure_relaxation_factor is not None:
        params["pressure_relaxation_factor"] = args.pressure_relaxation_factor
    if args.pressure_relaxation_initial_value is not None:
        params["pressure_relaxation_initial_value"] = args.pressure_relaxation_initial_value
    if args.pressure_relaxation_start_iteration is not None:
        params["pressure_relaxation_start_iteration"] = args.pressure_relaxation_start_iteration
    if args.pressure_relaxation_end_iteration is not None:
        params["pressure_relaxation_end_iteration"] = args.pressure_relaxation_end_iteration
    if args.velocity_relaxation_initial_value is not None:
        params["velocity_relaxation_initial_value"] = args.velocity_relaxation_initial_value
    if args.velocity_relaxation_start_iteration is not None:
        params["velocity_relaxation_start_iteration"] = args.velocity_relaxation_start_iteration
    if args.velocity_relaxation_end_iteration is not None:
        params["velocity_relaxation_end_iteration"] = args.velocity_relaxation_end_iteration
    if args.pressure_amg_cycle is not None:
        params["pressure_amg_cycle"] = args.pressure_amg_cycle
    if args.velocity_amg_cycle is not None:
        params["velocity_amg_cycle"] = args.velocity_amg_cycle
    if args.pressure_amg_max_cycles is not None:
        params["pressure_amg_max_cycles"] = args.pressure_amg_max_cycles
    if args.pressure_amg_converge_tol is not None:
        params["pressure_amg_converge_tol"] = args.pressure_amg_converge_tol
    if args.pressure_amg_epsilon is not None:
        params["pressure_amg_epsilon"] = args.pressure_amg_epsilon
    if args.amg_cycle is not None:
        params["amg_cycle"] = args.amg_cycle
    if args.amg_solver is not None:
        params["amg_solver"] = args.amg_solver
    for key, value in args.kv_pairs:
        params[key] = value

    if not params:
        print(
            "[ForceUpdate] ERROR: no parameters provided. "
            "Use --pressure-relaxation-factor, --pressure-relaxation-initial-value, "
            "--pressure-relaxation-start-iteration, --pressure-relaxation-end-iteration, "
            "--velocity-relaxation-initial-value, --velocity-relaxation-start-iteration, "
            "--velocity-relaxation-end-iteration, --pressure-amg-cycle, "
            "--pressure-amg-max-cycles, --pressure-amg-converge-tol, "
            "--pressure-amg-epsilon, "
            "--velocity-amg-cycle, --amg-cycle, --amg-solver, or --set KEY=VALUE.",
            file=sys.stderr,
        )
        return 2

    ai_cfg: dict[str, Any] = cfg.get("ai_optimization", {})
    constraints = ai_cfg.get("parameter_constraints", {})
    modifier = AICaseModifier(
        case_dir=case_dir,
        sim_type=case.simulation_type,
        constraints=constraints,
    )

    observations = {
        "forced_test": True,
        "source": "force_param_update.py",
        "requested": params,
    }
    applied = modifier.apply(
        params,
        current_values=None,
        observations=observations,
        trigger_iteration=args.trigger_iteration if args.trigger_iteration >= 0 else None,
    )

    print("[ForceUpdate] requested:")
    print(json.dumps(params, ensure_ascii=False, indent=2))
    print("[ForceUpdate] applied:")
    print(json.dumps(applied, ensure_ascii=False, indent=2))

    if not applied:
        print(
            "[ForceUpdate] WARNING: no parameters survived validation/clamping; "
            "check ai_update_history.jsonl for details.",
            file=sys.stderr,
        )
        return 3

    print(f"[ForceUpdate] wrote: {case_dir / AICaseModifier.PARAM_UPDATE_FILE}")
    print(f"[ForceUpdate] history: {case_dir / AICaseModifier.HISTORY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
