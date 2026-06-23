#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from generic_automation.monitor.ai_case_modifier import AICaseModifier
from generic_automation.monitor.ai_parameter_generator import AIParameterGenerator
from generic_automation.core.project_config import load_config, parse_case, resolve_case_dir
from generic_automation.core.runtime_metadata import load_or_create_run_context, update_run_context
from generic_automation.adapters.simulation_adapter import SimulationAdapter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _flag_enabled(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _build_monitor(
    ai_cfg: dict,
    case_dir: Path,
    case: object,
    run_context: dict,
) -> AIParameterGenerator:
    modifier = AICaseModifier(
        case_dir=case_dir,
        sim_type=case.simulation_type,
        constraints=ai_cfg.get("parameter_constraints", {}),
        run_context=run_context,
    )
    return AIParameterGenerator(
        ai_config=ai_cfg,
        case_dir=case_dir,
        case=case,
        modifier=modifier,
        run_context=run_context,
    )


def _monitor_requested(ai_cfg: dict, no_monitor: bool) -> bool:
    return not no_monitor and _flag_enabled(ai_cfg.get("enabled", True), default=True)


def _resolve_check_interval(cfg: dict, ai_cfg: dict) -> int:
    value = ai_cfg.get("check_interval")
    if value is None:
        value = cfg.get("check_interval", 500)
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one simulation case.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument(
        "--no-monitor",
        action="store_true",
        help="Disable the embedded RL monitor for this run.",
    )
    parser.add_argument(
        "--run-mode",
        default=None,
        help="Override case.run_mode with full_run, mesh_only, solve_only, or resume.",
    )
    parser.add_argument(
        "--input-sim",
        default=None,
        help="Override case.input_sim for solve_only or resume modes.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    cfg = load_config(config_path)
    case = parse_case(cfg)
    case.config_path = str(config_path)
    case.config_dir = str(config_path.parent)
    if args.run_mode is not None:
        case.run_mode = str(args.run_mode).strip().lower()
    if args.input_sim is not None:
        input_override = Path(args.input_sim).expanduser()
        if not input_override.is_absolute():
            input_override = input_override.resolve()
        case.input_sim = str(input_override)
    ai_cfg = dict(cfg.get("ai_optimization") or {})

    case_dir = resolve_case_dir(config_path, cfg, case.case_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    run_context = load_or_create_run_context(
        case_dir=case_dir,
        case=case,
        entrypoint="run_case.py",
        input_sim=case.input_sim,
    )
    monitor: AIParameterGenerator | None = None
    if _monitor_requested(ai_cfg, args.no_monitor):
        monitor = _build_monitor(ai_cfg, case_dir, case, run_context)
        monitor.start()
        log.info("Embedded RL monitor started for case %s", case.case_name)

    check_interval = _resolve_check_interval(cfg, ai_cfg)
    adapter = SimulationAdapter(
        str(cfg.get("adapter", "starccm")),
        check_interval=check_interval,
    )
    run_status = "failed"
    try:
        adapter.run(case, case_dir, run_context=run_context)
        run_status = "completed"
    finally:
        if monitor is not None:
            monitor.stop()
        update_run_context(case_dir, status=run_status)

    print(f"Finished: {case.case_name}  →  {case_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
